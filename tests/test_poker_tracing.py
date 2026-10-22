"""실제 LangSmith SDK의 병렬 추적 배선. 전송 함수는 대체하고 모든 HTTP를 차단한다."""
import asyncio
from copy import deepcopy
from contextlib import ExitStack
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from langsmith import Client, tracing_context

from agents import GameConfig, Table
from agents.demo import ScriptedPokerLLM
from agents.graph import build_graph
from agents.runner import RoundRunner


class PokerTracingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('requests.sessions.Session.request',
                                      side_effect=AssertionError('unexpected HTTP request')))
        self.stack.enter_context(patch('httpx.post', side_effect=AssertionError('unexpected model call')))
        self.created, self.updated = [], []
        def create(client, **kwargs):
            self.created.append(deepcopy(kwargs))
        def update(client, run_id, **kwargs):
            self.updated.append((run_id, deepcopy(kwargs)))
        self.stack.enter_context(patch.object(Client, 'create_run', new=create))
        self.stack.enter_context(patch.object(Client, 'update_run', new=update))
        self.client = Client(api_key='test-only', auto_batch_tracing=False)
        self.addCleanup(self.client.close)
        self.stack.enter_context(tracing_context(enabled=True, client=self.client, project_name='poker-tracing-test'))

    def table(self):
        return Table(['A', 'B', 'C'], config=GameConfig(data_dir=Path(self.folder.name), target_chips=999))

    async def test_parallel_graphs_share_round_parent_and_keep_player_event_metadata(self):
        table = self.table()
        b_started = asyncio.Event()
        class Backend(ScriptedPokerLLM):
            async def decide(self, observation, research, force_bet=False, feedback=''):
                if observation.required_game_utterances:
                    if self.player_id == 'A':
                        await b_started.wait()
                    elif self.player_id == 'B':
                        b_started.set()
                return await super().decide(observation, research, force_bet, feedback)
        runner = RoundRunner(table, {pid: build_graph(Backend(table, None, pid, None)) for pid in table.players})
        await asyncio.wait_for(runner.run_round(), 5)
        self.assertEqual(runner.errors, {})
        roots = [r for r in self.created if r['name'] == 'poker.round']
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0]['inputs'], {'publish_memory': False})
        graphs = [r for r in self.created if r['name'] in {'poker.A', 'poker.B', 'poker.C'}]
        self.assertEqual({r['name'] for r in graphs}, {'poker.A', 'poker.B', 'poker.C'})
        self.assertTrue(any(r['inputs']['observation']['phase'] == 'revealed' for r in graphs))
        for graph in graphs:
            self.assertEqual(graph['parent_run_id'], roots[0]['id'])
            metadata = graph['extra']['metadata']
            observation = graph['inputs']['observation']
            self.assertEqual(metadata['game_id'], table.game_id)
            self.assertEqual(metadata['player_id'], graph['name'].split('.')[1])
            self.assertEqual(metadata['player_id'], observation['player_id'])
            self.assertEqual(metadata['round_id'], 1)
            self.assertEqual(metadata['basis_event'], observation['event_id'])
            self.assertEqual(metadata['event_type'], graph['inputs']['event']['type'])
            nodes = [r for r in self.created if r.get('parent_run_id') == graph['id']]
            self.assertTrue({'route', 'answer', 'validate', 'remember'} <= {r['name'] for r in nodes})

    async def test_next_round_has_a_separate_root_and_updated_round_metadata(self):
        table = self.table()
        runner = RoundRunner(table, {pid: build_graph(ScriptedPokerLLM(table, None, pid, None))
                                    for pid in table.players})
        await runner.run_round()
        await runner.run_round()
        roots = [r for r in self.created if r['name'] == 'poker.round']
        self.assertEqual(len(roots), 2)
        self.assertNotEqual(roots[0]['id'], roots[1]['id'])
        for number, root in enumerate(roots, 1):
            graphs = [r for r in self.created if r.get('parent_run_id') == root['id']]
            self.assertTrue(graphs)
            self.assertTrue(all(r['extra']['metadata']['round_id'] == number for r in graphs))

    async def test_tracing_can_be_disabled_without_changing_game_execution(self):
        table = self.table()
        runner = RoundRunner(table, {pid: build_graph(ScriptedPokerLLM(table, None, pid, None))
                                    for pid in table.players})
        with tracing_context(enabled=False):
            await runner.run_round()
        self.assertEqual(table.round.phase, 'revealed')
        self.assertEqual(self.created, [])

    async def test_experiment_graphs_keep_trial_metadata_and_existing_node_timings(self):
        from experiments.common import ExperimentConfig
        from experiments.run import run
        with redirect_stdout(StringIO()):
            root = await run(ExperimentConfig(stage=2, cases=1, backend='fake',
                                             output=Path(self.folder.name)))
        manifest = json.loads((root / 'manifest.json').read_text())
        self.assertEqual(manifest['status'], 'complete')
        graphs = [r for r in self.created if r['name'] == 'poker.A']
        self.assertEqual(len(graphs), 4)
        for graph in graphs:
            metadata = graph['extra']['metadata']
            self.assertEqual(metadata['experiment_run_id'], manifest['run_id'])
            self.assertEqual(metadata['stage'], 2)
            self.assertEqual(metadata['player_id'], 'A')
            self.assertTrue(metadata['trial_id'])
            self.assertTrue(metadata['decision_id'])
        timings = [json.loads(s) for s in (root / 'timings.jsonl').read_text().splitlines()]
        self.assertEqual(len([r for r in timings if r['kind'] == 'node']), 20)


class ExperimentTracingConfigTests(unittest.TestCase):
    def test_cli_preserves_dotenv_tracing_and_flushes_without_running_models(self):
        from experiments.run import main
        with patch.dict(os.environ, {'LANGSMITH_TRACING': 'false', 'LANGCHAIN_TRACING_V2': 'false'}), \
             patch('dotenv.load_dotenv', side_effect=lambda: os.environ.update(
                 LANGSMITH_TRACING='true', LANGSMITH_PROJECT='poker-test')), \
             patch('sys.argv', ['experiments.run', '--stage', '3', '--cases', '1', '--backend', 'fake']), \
             patch('experiments.run.run', new=AsyncMock(return_value=Path('/unused'))), \
             patch('experiments.run.read_rows', return_value=[{'valid': True}]), \
             patch('experiments.run.wait_for_all_tracers') as flush:
            self.assertEqual(main(), 0)
            self.assertEqual(os.environ['LANGSMITH_TRACING'], 'true')
            self.assertEqual(os.environ['LANGSMITH_PROJECT'], 'poker-test')
            flush.assert_called_once()


if __name__ == '__main__':
    unittest.main()
