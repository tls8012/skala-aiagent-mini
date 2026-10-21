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

    def test_claims_and_betting_have_separate_candidate_pools(self):
        rows = [self.record('B', number) for number in range(1, 4)] + [self.record('D', 4)]
        for row in rows:
            for event in row['events']:
                if event['type'] == 'Speech' and event['player_id'] == 'B':
                    event['text'] = '네 카드는 J 이상이야.'
        self.tools._write(self.tools._experience_path('game'), json.dumps(rows))
        # 베팅 벡터가 질의에 더 가깝더라도 claims 검색에는 들어갈 수 없다.
        def embed(texts):
            return [[0.0, 1.0, 0.0] if '"type":"Speech"' in text else [1.0, 0.0, 0.0]
                    for text in texts]
        with patch.object(self.tools, '_embed', side_effect=embed), \
             patch.object(self.tools, '_queue', return_value=True):
            self.tools.index_records('game', rows)
            mixed = self.tools.search_experiences('game', 'A', 1, 'J 이상', 3, opponent_id='B')
            self.assertTrue(all(m['metadata']['event_type'] == 'BetPlaced' for m in mixed['matches']))
            for kind, expected in [('claims', 'Speech'), ('betting', 'BetPlaced')]:
                result = self.tools.search_experiences('game', 'A', 1, 'J 이상', 3,
                                                      opponent_id='B', search_kind=kind)
                self.assertEqual(result['status'], 'ready', result)
                self.assertEqual(len(result['matches']), 3)
                for match in result['matches']:
                    self.assertEqual(match['metadata']['event_type'], expected)
                    self.assertEqual(match['metadata']['player_id'], 'B')
                    self.assertEqual(match['round_context']['round_id'], match['metadata']['round_id'])
                    if kind == 'claims':
                        self.assertIn('네 카드는 J 이상이야.', match['text'])

    def test_long_event_does_not_fill_return_slots_and_is_restored_without_truncation(self):
        self.tools.chunker.max_bytes = 400
        rows = [self.record('B', number) for number in range(1, 4)]
        target = next(e for e in rows[0]['events'] if e['type'] == 'Speech' and e['player_id'] == 'B')
        target['text'] = '매우 긴 주장과 그 근거. ' * 300
        document = next(d for d in self.tools.chunker.events(rows[0]) if d.event_id == target['event_id'])
        self.tools._write(self.tools._experience_path('game'), json.dumps(rows))
        def embed(texts):
            return [[1.0, 0.0, 0.0] if text.startswith((document.prefix, 'source=query '))
                    else [0.0, 1.0, 0.0] for text in texts]
        with patch.object(self.tools, '_embed', side_effect=embed), \
             patch.object(self.tools, '_queue', return_value=True):
            self.tools.index_records('game', rows)
            with patch.object(self.tools._collection('game'), 'query', wraps=self.tools._collection('game').query) as query:
                result = self.tools.search_experiences('game', 'A', 1, '긴 주장', 3,
                                                      opponent_id='B', search_kind='claims')
            self.assertGreater(query.call_count, 1)  # 긴 사건이 후보를 독점하면 더 가져온다.
            self.assertEqual(len(result['matches']), 3)
            self.assertEqual(len({m['id'] for m in result['matches']}), 3)
            self.assertEqual({m['metadata']['experience_id'] for m in result['matches']},
                             {'played-1', 'played-2', 'played-3'})
            restored = next(m for m in result['matches'] if m['id'] == document.key)
            self.assertEqual(restored['text'], document.prefix + document.text)
            self.assertEqual(json.loads(restored['text'].split('\n', 1)[1])['text'], target['text'])
            self.assertGreater(restored['metadata']['chunk_count'], 3)

    def test_empty_claim_pool_does_not_fall_back_to_bets(self):
        row = self.record('B', 1)
        row['events'] = [e for e in row['events'] if e['type'] != 'Speech']
        self.tools._write(self.tools._experience_path('game'), json.dumps([row]))
        with patch.object(self.tools, '_embed', side_effect=lambda texts: [[1.0, 0.5, 0.25] for _ in texts]) as embed, \
             patch.object(self.tools, '_queue', return_value=True):
            self.tools.index_records('game', [row])
            before = embed.call_count
            result = self.tools.search_experiences('game', 'A', 1, '주장', opponent_id='B', search_kind='claims')
            self.assertEqual(result, {'status': 'ready', 'matches': []})
            self.assertEqual(embed.call_count, before)
        for invalid in ('unknown', ''):
            with self.assertRaises(ValueError):
                self.tools.search_experiences('game', 'A', 1, '주장', search_kind=invalid)


class ResearchBindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_research_binds_speaker_caches_by_opponent_and_forwards_context_to_model(self):
        with TemporaryDirectory() as folder:
            table = Table(['A', 'B', 'D'], config=GameConfig(data_dir=Path(folder),
                          retrieval_claim_limit=6, retrieval_betting_limit=4))
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
            self.assertEqual([(c[1]['search_kind'], c[1]['limit']) for c in mcp.calls],
                             [('claims', 6), ('betting', 4)])
            await backend.research(observation, event)
            self.assertEqual(len(mcp.calls), 2)
            await backend.research(observation, {**event, 'player_id': 'D'})
            self.assertEqual(mcp.calls[-1][1]['opponent_id'], 'D')
            self.assertEqual(len(mcp.calls), 4)
            await backend.decide(observation, research)
            self.assertEqual(llm.input['past_experiences']['matches'][0]['round_context'], context)
            self.assertEqual([m['search_kind'] for m in llm.input['past_experiences']['matches']],
                             ['claims', 'betting'])

    async def test_bet_without_prior_speech_still_searches_betting_and_retries_unready_results(self):
        with TemporaryDirectory() as folder:
            table = Table(['A', 'B'], config=GameConfig(data_dir=Path(folder)))
            table.start_round()
            class MCP:
                calls = []
                async def call_tool(self, name, arguments, server):
                    self.calls.append(arguments)
                    return {'status': 'indexing' if len(self.calls) == 1 else 'ready', 'matches': []}
            mcp = MCP()
            backend = PokerLLM(table, mcp, 'A', None)
            observation = table.observe('A')
            self.assertEqual((await backend.research(observation, {}))['status'], 'skipped')
            event = {'type': 'BetPlaced', 'player_id': 'B', 'action': 'check'}
            self.assertEqual((await backend.research(observation, event))['status'], 'indexing')
            self.assertEqual((await backend.research(observation, event))['status'], 'ready')
            await backend.research(observation, event)
            self.assertEqual(len(mcp.calls), 2)
            self.assertTrue(all(c['search_kind'] == 'betting' and c['opponent_id'] == 'B' for c in mcp.calls))

    async def test_bet_change_refreshes_betting_search_but_keeps_claim_search(self):
        with TemporaryDirectory() as folder:
            table = Table(['A', 'B'], config=GameConfig(data_dir=Path(folder)))
            table.start_round()
            class MCP:
                calls = []
                async def call_tool(self, name, arguments, server):
                    self.calls.append(arguments)
                    kind = arguments['search_kind']
                    return {'status': 'unavailable' if kind == 'betting' and len(self.calls) == 2 else 'ready',
                            'matches': []}
            mcp = MCP()
            backend = PokerLLM(table, mcp, 'A', None)
            observation = table.observe('A')
            event = {'type': 'Speech', 'channel': 'game', 'player_id': 'B', 'event_id': 7, 'text': 'J 이상이야.'}
            first = await backend.research(observation, event)
            self.assertEqual(first['status'], 'unavailable')
            self.assertEqual(first['searches']['claims']['status'], 'ready')
            self.assertEqual((await backend.research(observation, event))['status'], 'ready')
            updated = observation.model_copy(update={'current_bet': observation.current_bet + 2})
            await backend.research(updated, event)
            self.assertEqual([c['search_kind'] for c in mcp.calls], ['claims', 'betting', 'betting', 'betting'])

    def test_retrieval_config_limits_are_validated(self):
        for setting in ('retrieval_claim_limit', 'retrieval_betting_limit'):
            for invalid in (0, 21, True):
                with self.assertRaises(ValueError):
                    GameConfig(**{setting: invalid})
        with self.assertRaises(ValueError):
            GameConfig(retrieval_candidate_multiplier=0)


if __name__ == '__main__':
    unittest.main()
