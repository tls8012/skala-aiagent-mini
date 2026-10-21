"""템플릿 렌더링, 강제 행동의 체크/콜/폴드 접수, 실험 템플릿 추적 검사. 모델 호출 없음."""
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agents import GameConfig, Table
from agents.graph import build_graph
from agents.models import AgentDecision, Bet, PlayerCommand
from agents.poker_llm import PokerLLM
from agents.prompts import render_prompt
from agents.runner import RoundRunner
from experiments.common import ExperimentConfig, RunRecorder, digest


class TemplateTests(unittest.TestCase):
    def test_variables_are_required_and_inserted_values_are_not_reinterpreted(self):
        with self.assertRaises(KeyError):
            render_prompt('poker_mixed', player_id='A')
        with self.assertRaises(ValueError):
            render_prompt('../poker_mixed')
        prompt = render_prompt('private_memories', system='JSON {"key":"$value"}',
                               long_memo='${speaker_id}', game_memo='$5 {사실}', round_memo='현재 판단')
        for literal in ('JSON {"key":"$value"}', '${speaker_id}', '$5 {사실}', '현재 판단'):
            self.assertIn(literal, prompt)
        self.assertIn('당신은 A이고 원문 발언자는 human이다.',
                      render_prompt('poker_mixed', player_id='A', speaker_id='human'))

    def test_experiment_manifest_records_template_hashes(self):
        with TemporaryDirectory() as folder:
            recorder = RunRecorder(ExperimentConfig(stage=3, cases=10, backend='fake', output=Path(folder)))
            path = Path('agents/prompts/poker_action_selection.txt')
            self.assertEqual(recorder.manifest['source_hashes'][str(path)], digest(path.read_text()))
            self.assertIn('agents/prompts/__init__.py', recorder.manifest['source_hashes'])
            self.assertEqual(recorder.manifest['planned_trials'], 40)


class CompleteActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_waiting_reaches_runner_force_bet_then_check_and_call(self):
        from experiments.opponents import FixedPolicy
        with TemporaryDirectory() as folder:
            table = Table(['A', 'B'], config=GameConfig(data_dir=Path(folder)))
            class MCP:
                async def call_tool(self, name, arguments, server):
                    return {'status': 'ready', 'matches': []}
            class LLM:
                calls = []
                async def complete(self, system, user, schema=None):
                    data = json.loads(user)
                    observation = data['observation']
                    if observation['phase'] == 'revealed':
                        return json.dumps(dict.fromkeys(('round', 'game', 'long'), '')), {}
                    bet = None
                    if data['force_bet'] and observation['allowed_bets']:
                        action = 'check' if 'check' in observation['allowed_bets'] else 'call'
                        bet = {'action': action, 'raise_size': None}
                    self.calls.append({'forced': data['force_bet'], 'allowed': observation['allowed_bets'],
                                       'chosen': bet, 'system': system})
                    required = observation['required_game_utterances'] > 0
                    return json.dumps({'speech': 'speak' if required else 'silent',
                                       'text': '먼저 판단을 기다릴게.' if required else '', 'bet': bet,
                                       'wait_for_dialogue': bet is None, 'memory_updates': {}}), {}
            llm = LLM()
            policy = FixedPolicy('B', 'aggressive', 42, 's3-B-01')
            runner = RoundRunner(table, {'A': build_graph(PokerLLM(table, MCP(), 'A', llm)), 'B': policy},
                                 on_event=policy.on_event)
            await asyncio.wait_for(runner.run_round(), 5)
            self.assertEqual(runner.errors, {})
            waiting = [c for c in llm.calls if not c['forced'] and c['allowed']]
            self.assertTrue(waiting)
            self.assertTrue(all(c['chosen'] is None for c in waiting))
            forced = [c for c in llm.calls if c['forced']]
            self.assertTrue(forced)
            self.assertTrue(all(render_prompt('poker_action_selection') in c['system'] for c in forced))
            actions = [e['action'] for e in table.events if e['type'] == 'BetPlaced' and e['player_id'] == 'A']
            self.assertEqual(actions, ['check', 'call'])

    async def test_force_bet_allows_check_call_and_fold_through_graph_and_engine(self):
        for action in ('check', 'call', 'fold'):
            with self.subTest(action=action), TemporaryDirectory() as folder:
                table = Table(['A', 'B'], config=GameConfig(data_dir=Path(folder)))
                table.start_round()
                def submit(pid, kind, **arguments):
                    result = table.submit(pid, PlayerCommand(request_id=f'{pid}-{kind}', round_id=1,
                        basis_event=table.event_id, kind=kind, **arguments))
                    self.assertEqual(result.status, 'accepted')
                for pid in ('A', 'B'):
                    submit(pid, 'speak', text='이번 판을 판단할게.')
                if action == 'call':
                    submit('A', 'bet', bet=Bet(action='check'))
                    submit('B', 'bet', bet=Bet(action='raise', raise_size='small'))
                class MCP:
                    async def call_tool(self, name, arguments, server):
                        return {'status': 'ready', 'matches': []}
                class LLM:
                    async def complete(self, system, user, schema=None):
                        self.system, self.user = system, json.loads(user)
                        return json.dumps({'speech': 'silent', 'text': '', 'bet': {'action': action, 'raise_size': None},
                                           'wait_for_dialogue': False, 'memory_updates': {}}), {}
                llm = LLM()
                backend = PokerLLM(table, MCP(), 'A', llm)
                output = await build_graph(backend).ainvoke({'observation': table.observe('A').model_dump(),
                    'event': table.events[-1], 'force_bet': True})
                self.assertTrue(llm.user['force_bet'])
                self.assertIn(render_prompt('poker_action_selection'), llm.system)
                self.assertNotIn('${action_selection}', llm.system)
                self.assertEqual(output['decision']['bet']['action'], action)
                results = table.apply_decision('A', AgentDecision.model_validate(output['decision']), 'decision')
                self.assertTrue(all(r.status == 'accepted' for r in results), results)
                bets = [e for e in table.events if e['type'] == 'BetPlaced' and e['player_id'] == 'A']
                self.assertEqual(bets[-1]['action'], action)


if __name__ == '__main__':
    unittest.main()
