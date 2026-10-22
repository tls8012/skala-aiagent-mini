"""복합 카드 질문의 게임 경로, 참가자 중립 입력과 로컬 진단 기록."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from agents import GameConfig, Table
from agents.graph import build_graph
from agents.models import AgentDecision, PlayerCommand
from agents.poker_llm import PokerLLM
from agents.runner import RoundRunner
from agents.speech_interpreter import SpeechInterpreter
from agents.tracing import configure_tracing


class RuntimeRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_card_question_and_weather_use_mixed_game_decision_without_human_kind(self):
        with TemporaryDirectory() as folder:
            table = Table(['A', 'B'], 'zz', config=GameConfig(data_dir=Path(folder)))
            table.start_round()
            text = '내 카드 알려줘. 그리고 너희 오늘 날씨는 어떻다고 생각해?'
            class InterpreterClient:
                def complete(self, system, user, schema=None):
                    assert '자기 카드나 상대 카드를 알려 달라는 질문도 game' in system
                    return json.dumps({'intents': [
                        {'kind': 'game', 'targets': ['A', 'B'], 'text': '내 카드 알려줘.'},
                        {'kind': 'chat', 'targets': ['A', 'B'], 'text': '그리고 너희 오늘 날씨는 어떻다고 생각해?'}]}), {}
            calls = []
            class LLM:
                async def complete(self, system, user, schema=None):
                    calls.append((system, json.loads(user)))
                    return json.dumps({'speech': 'speak', 'text': '날씨 도구는 없어. 카드 정보는 협상해 보자.',
                        'response_channel': 'mixed', 'bet': None, 'wait_for_dialogue': False}), {}
            class Backend(PokerLLM):
                async def research(self, observation, event):
                    return {'status': 'skipped', 'matches': []}
            backend = Backend(table, None, 'A', LLM())
            runner = RoundRunner(table, {'A': build_graph(backend)},
                                 interpreter=SpeechInterpreter(table.config, InterpreterClient()))
            command = PlayerCommand(request_id='card-and-weather', round_id=1,
                                    basis_event=table.event_id, kind='speak', text=text)
            self.assertEqual((await runner._prepare_human_command('zz', command)).status, 'accepted')
            event = table.events[-1]
            self.assertEqual(event['channel'], 'game')
            observation = table.observe('A')
            result = await runner._invoke_graph('A', observation, event, False)
            self.assertEqual(backend.route(observation, event), 'mixed')
            decision = AgentDecision.model_validate(result['decision'])
            self.assertEqual(table.apply_decision('A', decision, 'reply')[0].status, 'accepted')
            system, user = calls[0]
            self.assertIn('동등한 경쟁자', system)
            self.assertNotIn('human_request', user)
            self.assertEqual(user['addressed_request']['player_id'], 'zz')
            self.assertTrue(all('kind' not in p for p in user['observation']['players']))
            self.assertEqual(next(p for p in table.observe('A').players if p['id'] == 'zz')['kind'], 'human')
            self.assertEqual(table.round.conversation.counts['A'], 1)
            records = [json.loads(s) for s in (table.archive_dir / 'runtime.jsonl').read_text().splitlines()]
            self.assertEqual([r['type'] for r in records], ['input_interpreted', 'graph_route', 'graph_result'])
            self.assertEqual(records[1]['route'], 'mixed')
            self.assertEqual(records[2]['result']['decision']['channel'], 'mixed')


class TracingStartupTests(unittest.TestCase):
    def test_startup_reports_configuration_but_never_key_values(self):
        with patch.dict(os.environ, {'LANGSMITH_TRACING': 'true', 'LANGSMITH_PROJECT': 'fixture-project',
                                    'LANGSMITH_API_KEY': 'test-secret-do-not-print'}, clear=True), \
             patch('agents.tracing.load_dotenv'), self.assertLogs('poker', 'INFO') as logs:
            status = configure_tracing()
        self.assertTrue(status['enabled'])
        self.assertTrue(status['api_key_configured'])
        self.assertEqual(status['project'], 'fixture-project')
        self.assertIn('api_key_configured=True', ' '.join(logs.output))
        self.assertNotIn('test-secret-do-not-print', ' '.join(logs.output))

    def test_missing_key_is_visible_in_startup_warning(self):
        with patch.dict(os.environ, {'LANGSMITH_TRACING': 'true'}, clear=True), \
             patch('agents.tracing.load_dotenv'), self.assertLogs('poker', 'WARNING') as logs:
            status = configure_tracing()
        self.assertFalse(status['api_key_configured'])
        self.assertIn('no API key', ' '.join(logs.output))


if __name__ == '__main__':
    unittest.main()
