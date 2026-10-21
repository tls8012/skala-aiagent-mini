"""단계 공통 실행. 저장소를 격리하고 동일 사례의 입력을 대응시킨다."""
import asyncio
from copy import deepcopy
from dataclasses import asdict, replace
import json
import random
from uuid import uuid4

from agents.mcp_server import GameTools
from agents.models import AgentDecision
from agents.runner import RoundRunner
from .backends import ToolGate, make_graph, memory_connection, prepare_tools
from .common import CONDITIONS, digest, plain
from .fixtures import build_table, fixed_evidence, history_bank, input_hash, memoir
from .opponents import FixedPolicy


async def run_cases(recorder, cases):
    config = recorder.config
    expected = {}
    banks = {}
    rng = random.Random(config.seed)
    for case in cases:
        bank_key = case.opponent if config.stage == 3 else case.case_id
        if bank_key not in banks:
            with recorder.span('preparation', 'history_bank', case_id=case.case_id):
                bank_case = replace(case, seed=config.seed+30000, case_id=f'bank-{case.opponent}', index=0) if config.stage == 3 else case
                rows, provenance = history_bank(recorder.game_config('fixture-' + bank_key), bank_case, stage=config.stage)
                unrelated = None
                if config.stage == 1:
                    other = replace(case, opponent='D', family='AGGRESSION')
                    unrelated, _ = history_bank(recorder.game_config('unrelated-' + case.case_id), other, stage=config.stage)
                banks[bank_key] = rows, provenance, unrelated
        rows, provenance, unrelated = banks[bank_key]
        arms = list(config.arms)
        rng.shuffle(arms)
        for condition in arms:
            trial_id = f'{case.case_id}-{condition}'
            print(f'[{config.stage}] {trial_id}', flush=True)
            row = {'trial_id': trial_id, 'case_id': case.case_id, 'condition': condition,
                   'stage': config.stage, 'opponent': case.opponent, 'seed': case.seed,
                   'backend': config.backend, 'scenario': asdict(case), 'valid': False}
            try:
                with recorder.span('trial', 'trial_total', trial_id=trial_id):
                    await run_trial(recorder, case, condition, trial_id, rows, unrelated, provenance, expected, row)
            except BaseException as error:
                row.update(valid=False, error=f'{type(error).__name__}: {error}')
                recorder.emit('errors', **row)
                if not isinstance(error, Exception):
                    raise
                print(f'  failed: {row["error"]}', flush=True)
            finally:
                recorder.emit('trials', **row)
                recorder.proposals.clear()


async def run_trial(recorder, case, condition, trial_id, rows, unrelated, provenance, expected, result):
    stage = recorder.config.stage
    game = recorder.game_config(trial_id)
    table = build_table(game, case, 'eval-' + trial_id, stage=stage, recorder=recorder, trial_id=trial_id)
    result['input_hash'] = input_hash(table)
    if case.case_id in expected and expected[case.case_id] != result['input_hash']:
        raise ValueError('paired observation mismatch')
    expected[case.case_id] = result['input_hash']
    result['initial_order'] = table.round.order
    result['game_id'] = table.game_id
    result['game_config'] = plain(asdict(game))
    result['initial_chips'] = game.initial_chips
    result['bank_hash'] = digest(rows)
    # 공개 아카이브를 기존 저장 계약으로 검증한다. default seed 자료를 생성하지 않도록 먼저 적재한다.
    public = []
    for item in rows:
        item = deepcopy(item)
        item['game_id'] = table.game_id
        public.append(GameTools._public_record(item))
    experiences = game.data_dir / 'experiences' / f'{table.game_id}.json'
    experiences.parent.mkdir(parents=True, exist_ok=True)
    experiences.write_text(json.dumps(public, ensure_ascii=False))
    (game.data_dir / 'fixture-provenance.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2))
    result['public_bank_hash'] = digest(public)
    search, memos_on = ((condition != 'no_information', False) if stage == 1 else CONDITIONS[condition])
    result.update(search_enabled=search, memo_input_enabled=memos_on,
                  retrieval_mode='fixture' if stage == 1 else 'smoke_fixture' if recorder.config.backend == 'fake' else 'chroma')
    evidence = fixed_evidence(unrelated if condition == 'unrelated_history' else rows, case.opponent,
                              unrelated=condition == 'unrelated_history')
    if condition == 'unrelated_history':
        evidence['matches'] = evidence['matches'][:len(fixed_evidence(rows, case.opponent)['matches'])]
    initial_memos = memoir(rows, case.opponent) if memos_on else dict.fromkeys(('round', 'game', 'long'), '')
    async with memory_connection(game, recorder.config.backend, recorder, trial_id, evidence) as client:
        async with asyncio.timeout(recorder.config.index_timeout):
            await prepare_tools(table, client, recorder, trial_id, initial_memos,
                                index=search and stage != 1 and recorder.config.backend == 'ollama',
                                timeout=recorder.config.index_timeout)
        gate = ToolGate(client, recorder, trial_id, read_memos=memos_on, write_memos=stage == 1 or memos_on)
        graph = make_graph(table, gate, recorder, trial_id, search_enabled=search,
                           fixed_research=evidence if search and stage == 1 else None)
        with recorder.span('execution', 'game_or_decision', trial_id=trial_id):
            async with asyncio.timeout(recorder.config.timeout):
                if stage == 3:
                    policy = FixedPolicy(case.opponent, 'aggressive' if case.opponent == 'B' else 'defensive',
                                         recorder.config.seed, case.case_id)
                    def on_event(event):
                        policy.on_event(event)
                        recorder.emit('events', trial_id=trial_id, event=event)
                    runner = RoundRunner(table, {'A': graph, case.opponent: policy}, on_event=on_event)
                    outcome = await runner.run_round()
                    final = table.players['A'].chips
                    if sum(p.chips for p in table.players.values()) + table.carry_pot != game.initial_chips*2:
                        raise ValueError('chip conservation failed')
                    if outcome['draw']:
                        raise ValueError('unexpected heads-up draw')
                    result.update(final_chips=final, chip_delta=final-game.initial_chips,
                                  winner=outcome['winner'], valid=not runner.errors,
                                  engine_result=outcome, runner_errors=runner.errors,
                                  policy_version=policy.version, runner_decisions=runner.decisions)
                    recorder.emit('rounds', **result)
                else:
                    state = {'observation': table.observe('A').model_dump(), 'event': table.events[-1], 'force_bet': True}
                    output = await graph.ainvoke(state)
                    decision = AgentDecision.model_validate(output['decision'])
                    applied = table.apply_decision('A', decision, uuid4().hex)
                    bet_events = [e for e in table.events if e['type'] == 'BetPlaced' and e['player_id'] == 'A']
                    result.update(valid=all(r.status == 'accepted' for r in applied),
                                  decision=decision.model_dump(), applied=[r.model_dump() for r in applied],
                                  betting_signature={'action': decision.bet.action if decision.bet else None,
                                                     'raise_size': decision.bet.raise_size if decision.bet else None,
                                                     'actual_increment': bet_events[-1]['amount'] if bet_events else None})
                # 마스킹 전 파일을 읽어 off 조건의 메모가 비어 있는지도 확인한다.
                saved = await client.call_tool('read_memories', {
                    'game_id': table.game_id, 'agent_id': 'A', 'round_id': table.round.number}, server='poker')
                result['saved_memos'] = saved
                if stage != 1 and not memos_on and any(saved.values()):
                    raise ValueError('memo-off trial leaked persisted memories')
                recorder.emit('contexts', trial_id=trial_id, saved_memos=saved)
