"""상대별 실제 Chroma 검색과 동일 과거 라운드의 공개 결과 연결. 모델/임베딩 HTTP 호출 없음."""
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from agents import GameConfig, Table
from agents.cards import Card
from agents.mcp_server import GameTools
from agents.models import Bet, PlayerCommand
from agents.poker_llm import PokerLLM


@unittest.skipUnless(importlib.util.find_spec('chromadb'), 'optional Chroma dependency is not installed')
class OpponentRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.config = GameConfig(data_dir=Path(self.folder.name))
        self.tools = GameTools(self.config)
        self.addCleanup(self.tools.close)

    def record(self, opponent, number, fold=False):
        table = Table(['A', opponent], config=self.config, game_id=f'history-{number}')
        table.start_round({'A': Card(9, 'H'), opponent: Card(4, 'S')})
        def submit(pid, kind, **arguments):
            result = table.submit(pid, PlayerCommand(request_id=f'{pid}-{kind}', round_id=1,
                basis_event=table.event_id, kind=kind, **arguments))
            self.assertEqual(result.status, 'accepted')
        submit('A', 'speak', text='판단을 기다릴게.')
        submit(opponent, 'speak', text='네 카드는 10 이하야.')
        submit('A', 'bet', bet=Bet(action='check'))
        submit(opponent, 'bet', bet=Bet(action='fold' if fold else 'check'))
        record = json.loads(next(table.archive_dir.glob('*.json')).read_text())
        record['round_id'] = number
        for event in record['events']:
            event['round_id'] = number
        return GameTools._public_record(record)

    def test_same_wording_from_other_players_is_filtered_and_public_results_are_attached(self):
        rows = [self.record('B', 1), self.record('D', 2), self.record('B', 3, fold=True)]
        self.tools._write(self.tools._experience_path('game'), json.dumps(rows))
        with patch.object(self.tools, '_embed', side_effect=lambda texts: [[1.0, 0.5, 0.25] for _ in texts]) as embed, \
             patch.object(self.tools, '_queue', return_value=True):
            self.tools.index_records('game', rows)
            before = embed.call_count
            result = self.tools.search_experiences('game', 'A', 1, '네 카드는 10 이하야', 20, opponent_id='B')
            self.assertEqual(result['status'], 'ready', result)
            self.assertTrue(result['matches'])
            self.assertEqual(embed.call_count, before + 1)  # 새 청크 임베딩 없이 질의만 임베딩
            by_id = {r['id']: r for r in rows}
            for match in result['matches']:
                meta, context = match['metadata'], match['round_context']
                self.assertEqual(meta['player_id'], 'B')
                self.assertIn(meta['event_type'], ('Speech', 'BetPlaced'))
                self.assertNotEqual(meta['experience_id'], 'played-2')
                self.assertEqual(context['experience_id'], meta['experience_id'])
                self.assertEqual(context['round_id'], meta['round_id'])
                self.assertEqual(context['result'], by_id[meta['experience_id']]['result'])
            folded = [m for m in result['matches'] if m['metadata']['experience_id'] == 'played-3']
            self.assertTrue(folded)
            self.assertNotIn('B', folded[0]['round_context']['result']['revealed_cards'])
            result['matches'][0]['round_context']['result']['winner'] = 'modified'
            fresh = self.tools.search_experiences('game', 'A', 1, '과거', 20, opponent_id='B')
            self.assertTrue(all(m['round_context']['result']['winner'] == 'A' for m in fresh['matches']))

    def test_missing_opponent_is_empty_without_fallback_or_query_embedding(self):
        rows = [self.record('D', 1)]
        self.tools._write(self.tools._experience_path('game'), json.dumps(rows))
        with patch.object(self.tools, '_embed', side_effect=lambda texts: [[1.0, 0.5, 0.25] for _ in texts]) as embed, \
             patch.object(self.tools, '_queue', return_value=True):
            self.tools.index_records('game', rows)
            before = embed.call_count
            result = self.tools.search_experiences('game', 'A', 1, '상대 B', opponent_id='B')
            self.assertEqual(result, {'status': 'ready', 'matches': []})
            self.assertEqual(embed.call_count, before)
        for invalid in ('A', '../B'):
            with self.assertRaises(ValueError):
                self.tools.search_experiences('game', 'A', 1, '상대', opponent_id=invalid)


class ResearchBindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_research_binds_speaker_caches_by_opponent_and_forwards_context_to_model(self):
        with TemporaryDirectory() as folder:
            table = Table(['A', 'B', 'D'], config=GameConfig(data_dir=Path(folder)))
            table.start_round()
            context = {'experience_id': 'played-8', 'round_id': 8,
                       'result': {'revealed_cards': {'A': {'rank': 13, 'suit': 'S'}}}}
            class MCP:
                calls = []
                async def call_tool(self, name, arguments, server):
                    self.calls.append((name, arguments, server))
                    return {'status': 'ready', 'matches': [{'round_context': context}]}
            class LLM:
                async def complete(self, system, user, schema=None):
                    self.input = json.loads(user)
                    return json.dumps({'speech': 'speak', 'text': '판단할게.', 'bet': None,
                                       'wait_for_dialogue': False, 'memory_updates': {}}), {}
            mcp, llm = MCP(), LLM()
            backend = PokerLLM(table, mcp, 'A', llm)
            observation = table.observe('A')
            event = {'type': 'Speech', 'channel': 'game', 'player_id': 'B', 'event_id': 7, 'text': '주장'}
            research = await backend.research(observation, event)
            self.assertEqual(mcp.calls[-1][1]['opponent_id'], 'B')
            self.assertEqual(mcp.calls[-1][1]['agent_id'], 'A')
            await backend.research(observation, event)
            self.assertEqual(len(mcp.calls), 1)
            await backend.research(observation, {**event, 'player_id': 'D'})
            self.assertEqual(mcp.calls[-1][1]['opponent_id'], 'D')
            self.assertEqual(len(mcp.calls), 2)
            await backend.decide(observation, research)
            self.assertEqual(llm.input['past_experiences']['matches'][0]['round_context'], context)


if __name__ == '__main__':
    unittest.main()
