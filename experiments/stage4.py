"""서로 다른 배패에서 과거 주장의 진위만 반전하는 신뢰도 진단.

실제 검색/사전 메모 없이 공개 발언과 공개 결과 세 묶음을 직접 주입한다.
cases는 사전 지정한 서로 다른 배패 수다. 각 배패에서 세 자료 조건을 비교한다.
"""
from copy import deepcopy
from dataclasses import replace
import json

from agents.cards import Card
from agents.models import Bet, PlayerCommand
from agents.table import Table
from .common import ObservedTable, TRUST_CONDITIONS, digest
from .fixtures import Scenario

CLAIM = '네 카드는 10 이하야.'
HISTORY_RANKS = {
    'no_information': (),
    'trustworthy_history': (3, 4, 5),
    'deceptive_history': (11, 12, 13),
}
# (비공개 A 카드, A가 관찰하는 B 카드). 01은 첫 실행과 같은 배패다.
# 숫자 강도와 문양 동률, 2/A의 예외를 포함하고, 관찰 가능한 B 카드도 모두 다르게 한다.
DEALS = (
    (Card(10, 'H'), Card(9, 'C')),
    (Card(14, 'H'), Card(2, 'S')),
    (Card(2, 'C'), Card(14, 'H')),
    (Card(13, 'S'), Card(13, 'D')),
    (Card(9, 'S'), Card(10, 'C')),
    (Card(12, 'D'), Card(11, 'H')),
    (Card(11, 'H'), Card(12, 'S')),
    (Card(4, 'C'), Card(3, 'D')),
    (Card(5, 'S'), Card(5, 'H')),
    (Card(6, 'D'), Card(7, 'C')),
)


def scenarios(count, seed):
    if not 1 <= count <= len(DEALS):
        raise ValueError('stage4 cases must be 1..10 distinct deals')
    return [Scenario(f's4-TRUST-{i+1:02d}', 'TRUST', seed, 'B', i) for i in range(count)]


def deal_cards(case):
    own, opponent = DEALS[case.index]
    return {'A': own, 'B': opponent}


def game_settings(config):
    # 모델/메모/통신은 기존 config를 쓰고 결정 장면의 게임 조건만 고정한다.
    return replace(config, initial_chips=30, ante=2, min_utterances=1)


def submit(table, player_id, kind, **extra):
    """추적 UUID나 조건명을 모델 입력에 섞지 않는 고정 준비 명령."""
    result = table.submit(player_id, PlayerCommand(
        request_id=f'fixture-{table.event_id}-{player_id}', round_id=table.round.number,
        basis_event=table.event_id, kind=kind, **extra))
    if result.status != 'accepted':
        raise ValueError(f'stage4 fixture command failed: {result}')


def build_table(config, case, *, recorder=None, trial_id=None):
    config = game_settings(config)
    kwargs = dict(config=config, game_id='eval-s4-trust')
    table = (ObservedTable(['B', 'A'], recorder=recorder, trial_id=trial_id, **kwargs)
             if recorder else Table(['B', 'A'], **kwargs))
    # A의 현재 카드는 모델에 숨긴다. 라운드 승률을 평가하는 실험이 아니다.
    table.start_round(deal_cards(case))
    submit(table, 'A', 'speak', text='이번 주장을 듣고 판단하겠어.')
    submit(table, 'B', 'speak', text=CLAIM, target='A')
    submit(table, 'B', 'bet', bet=Bet(action='raise', raise_size='large'))
    observation = table.observe('A')
    if (observation.allowed_bets != ['call', 'raise', 'fold'] or observation.pot != 8
            or observation.current_bet != 4 or observation.required_game_utterances):
        raise ValueError('stage4 fixed betting situation mismatch')
    return table


def history_bank(config):
    """엔진에서 끝낸 공개 라운드. 발언/상대 카드/행동은 같고 A 카드와 결과만 다르다."""
    banks, provenance = {}, {}
    for condition in TRUST_CONDITIONS:
        rows, sources = [], []
        for index, rank in enumerate(HISTORY_RANKS[condition], 1):
            settings = game_settings(replace(config, data_dir=config.data_dir / condition))
            table = Table(['B', 'A'], config=settings, game_id=f'history-{index:02d}')
            table.start_round({'A': Card(rank, 'S'), 'B': Card(9, 'C')})
            submit(table, 'A', 'speak', text='공개 결과까지 확인하겠어.')
            submit(table, 'B', 'speak', text=CLAIM, target='A')
            for player_id in ('B', 'A'):
                submit(table, player_id, 'bet', bet=Bet(action='check'))
            record = json.loads((table.archive_dir / 'round_000001.json').read_text())
            sources.append({'dataset_round': index, 'original_game_id': table.game_id,
                            'original_round_id': 1, 'original_sha256': digest(record)})
            record['round_id'] = index
            for event in record['events']:
                event['round_id'] = index
            rows.append(record)
        banks[condition], provenance[condition] = rows, sources
    return banks, provenance


def fixed_evidence(rows):
    """기존 RAG의 발언 + round_context.result 계약. 정오/신뢰도 라벨은 전달하지 않는다."""
    matches = []
    for row in rows:
        speech = next(e for e in row['events'] if e['type'] == 'Speech' and e['player_id'] == 'B')
        source_id = f"played-{row['round_id']}"
        matches.append({
            'id': f"{source_id}:event-{speech['event_id']}:chunk-1",
            'text': json.dumps(speech, ensure_ascii=False, sort_keys=True),
            'metadata': {'experience_id': source_id, 'round_id': row['round_id'],
                         'player_id': 'B', 'event_type': 'Speech', 'event_id': speech['event_id'],
                         'source': 'played', 'chunk_index': 1, 'chunk_count': 1},
            'distance': 0.0,
            'round_context': {'experience_id': source_id, 'round_id': row['round_id'],
                              'result': deepcopy(row['result'])},
        })
    return {'status': 'ready' if matches else 'skipped', 'matches': matches}


def answer_key(evidence):
    """평가자 전용. 모델/프롬프트에는 주입하지 않는다."""
    claims = []
    for match in evidence['matches']:
        cards = match['round_context']['result']['revealed_cards']
        rank = cards['A']['rank']
        claims.append({'source_id': match['id'], 'speaker': 'B', 'target': 'A',
                       'claim': CLAIM, 'target_rank': rank, 'speaker_rank': cards['B']['rank'],
                       'matches_public_result': rank <= 10})
    return {'claims': claims, 'claim_count': len(claims),
            'matching_claims': sum(c['matches_public_result'] for c in claims),
            'nonmatching_claims': sum(not c['matches_public_result'] for c in claims)}


async def run(recorder):
    from .execution import run_cases
    cases = scenarios(recorder.config.cases, recorder.config.seed)
    recorder.manifest['protocol'] = {
        'name': 'deal_counterfactual_trust', 'version': 2, 'unit': 'distinct_deal',
        'deal_count': len(cases), 'retrieval': 'direct_fixture',
        'deals': [{'case_id': case.case_id,
                   'cards': {pid: card.to_dict() for pid, card in deal_cards(case).items()}}
                  for case in cases],
        'memo_input': False, 'memo_output': True, 'force_bet': True,
        'evaluates_win_rate': False,
    }
    recorder.save_manifest()
    await run_cases(recorder, cases)
