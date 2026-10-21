"""새 엔진 없이 실험 배선/격리/계측/실패 집계가 실제로 동작하는지 검사한다."""
import asyncio
from collections import Counter, defaultdict
from contextlib import redirect_stdout
import csv
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from experiments.backends import LocalSmokeTools, ObservedGraph, RecordingClient, SmokeClient, prepare_tools
from experiments.common import ExperimentConfig, RunRecorder
from experiments.fixtures import build_table, scenarios
from experiments.report import read_rows, summarize
from experiments.run import run


class ExperimentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.env = patch.dict(os.environ, {'LANGSMITH_TRACING': 'false', 'LANGCHAIN_TRACING_V2': 'false'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def config(self, stage=1, cases=1, **kwargs):
        return ExperimentConfig(stage=stage, cases=cases, backend='fake', output=Path(self.folder.name), **kwargs)

    async def execute(self, config):
        with redirect_stdout(StringIO()):
            return await run(config)

    async def test_stage1_real_graph_nodes_applied_actions_and_written_memos(self):
        root = await self.execute(self.config(cases=5))
        trials = read_rows(root, 'trials')
        self.assertEqual(len(trials), 15)
        self.assertTrue(all(t['valid'] for t in trials))
        hashes = defaultdict(set)
        for trial in trials:
            hashes[trial['case_id']].add(trial['input_hash'])
            self.assertTrue(trial['saved_memos']['round'])
            self.assertTrue(all(r['status'] == 'accepted' for r in trial['applied']))
        self.assertTrue(all(len(values) == 1 for values in hashes.values()))
        nodes = [t for t in read_rows(root, 'timings') if t['kind'] == 'node']
        self.assertEqual(Counter(t['name'] for t in nodes), dict.fromkeys(('route','research','answer','validate','remember'), 15))
        self.assertTrue(all(t['duration_s'] >= 0 for t in nodes))
        proposed = {d['decision_id'] for d in read_rows(root, 'decisions') if d['event'] == 'proposed'}
        applied = {d['decision_id'] for d in read_rows(root, 'decisions') if d['event'] == 'applied'}
        self.assertEqual(proposed, applied)
        self.assertFalse(json.loads((root/'manifest.json').read_text())['evidence_run'])
        with (root/'responses.csv').open(newline='') as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 15)

    async def test_stage2_four_arms_mask_actual_model_inputs_and_off_files(self):
        root = await self.execute(self.config(stage=2))
        trials = read_rows(root, 'trials')
        by_id = {t['trial_id']: t for t in trials}
        self.assertEqual(len(trials), 4)
        for call in read_rows(root, 'model_calls'):
            if call['event'] != 'request':
                continue
            row = by_id[call['trial_id']]
            self.assertEqual('공개 과거 자료에서' in call['system'], row['memo_input_enabled'])
            self.assertEqual(bool(json.loads(call['user'])['past_experiences']['matches']), row['search_enabled'])
        for row in trials:
            self.assertTrue(row['valid'])
            if not row['memo_input_enabled']:
                self.assertFalse(any(row['saved_memos'].values()))
        tools = read_rows(root, 'tools')
        for t in tools:
            if t['name'] == 'search_experiences':
                self.assertTrue(by_id[t['trial_id']]['search_enabled'])

    async def test_stage3_heads_up_rounds_conserve_chips_and_opponents_use_no_tools_or_models(self):
        root = await self.execute(self.config(stage=3, cases=2))
        rows = read_rows(root, 'trials')
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(r['valid'] for r in rows))
        self.assertEqual({r['opponent'] for r in rows}, {'B','C'})
        self.assertEqual({tuple(r['initial_order']) for r in rows}, {('A','B'),('B','A'),('A','C'),('C','A')})
        for row in rows:
            self.assertEqual(row['final_chips']-30, row['chip_delta'])
        for call in read_rows(root, 'model_calls'):
            if call['event'] == 'request':
                observation = json.loads(call['user'])['observation']
                self.assertEqual(observation['player_id'], 'A')
                if observation['phase'] != 'revealed':
                    self.assertNotIn('A', observation['visible_cards'])
        for tool in read_rows(root, 'tools'):
            if 'arguments' in tool:
                self.assertEqual(tool['arguments']['agent_id'], 'A')
        summary = json.loads((root/'summary.json').read_text())
        self.assertTrue(all(r['actions'] for r in summary['conditions']))
        self.assertEqual(len(summary['paired']), 4)
        for opponent in ('B', 'C'):
            self.assertEqual(len({r['bank_hash'] for r in rows if r['opponent'] == opponent}), 1)

    async def test_late_audit_failure_marks_trial_invalid_even_after_action_was_accepted(self):
        original = LocalSmokeTools.call_tool
        async def leak(client, name, arguments, server='poker'):
            if name == 'read_memories':
                return {'round': '오염된 내용', 'game': '', 'long': ''}
            return await original(client, name, arguments, server)
        with patch.object(LocalSmokeTools, 'call_tool', leak):
            root = await self.execute(self.config(stage=2, conditions=('control',)))
        row = read_rows(root, 'trials')[0]
        self.assertFalse(row['valid'])
        self.assertIn('leaked', row['error'])

    async def test_interruption_preserves_attempted_trial_and_partial_report(self):
        with patch.object(ObservedGraph, 'ainvoke', side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await self.execute(self.config(conditions=('no_information',)))
        root = next(Path(self.folder.name).iterdir())
        rows = read_rows(root, 'trials')
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['valid'])
        self.assertIn('CancelledError', rows[0]['error'])
        self.assertEqual(json.loads((root/'summary.json').read_text())['attempted'], 1)

    async def test_bad_model_outputs_are_logged_with_all_retries_not_silently_replaced(self):
        with patch.object(SmokeClient, 'complete', return_value=('not json', {'tokens': 0})):
            root = await self.execute(self.config(conditions=('no_information',)))
        trials = read_rows(root, 'trials')
        self.assertEqual(len(trials), 1)
        self.assertFalse(trials[0]['valid'])
        self.assertEqual(len([r for r in read_rows(root, 'model_calls') if r['event'] == 'request']), 3)
        self.assertEqual(json.loads((root/'manifest.json').read_text())['status'], 'complete_with_failures')
        self.assertTrue(any(t['kind'] == 'node' and t['status'] == 'error' for t in read_rows(root, 'timings')))

    async def test_report_regeneration_preserves_human_review(self):
        root = await self.execute(self.config(conditions=('no_information',)))
        path = root/'review.csv'
        with path.open(newline='') as handle:
            reader = csv.DictReader(handle)
            fields, rows = reader.fieldnames, list(reader)
        rows[0]['notes'] = '사람이 작성한 검토'
        with path.open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader(); writer.writerows(rows)
        saved = path.read_text()
        summarize(root)
        self.assertEqual(path.read_text(), saved)

    async def test_cancelled_awaiter_still_collects_inflight_model_response_and_time(self):
        recorder = RunRecorder(self.config())
        entered = threading.Event()
        class Slow:
            name = 'slow'
            def complete(self, *args, **kwargs):
                entered.set()
                time.sleep(0.05)
                return '{}', {'tokens': 1}
        client = RecordingClient(Slow(), recorder, 'trial')
        task = asyncio.create_task(asyncio.to_thread(client.complete, 's', 'u'))
        while not entered.is_set():
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await recorder.drain_models()
        self.assertFalse(recorder.active_models)
        self.assertEqual([r['event'] for r in read_rows(recorder.root, 'model_calls')], ['request','response'])
        self.assertGreaterEqual(read_rows(recorder.root, 'timings')[0]['duration_s'], 0.045)

    async def test_index_probe_waits_for_queued_index_job_instead_of_restarting_forever(self):
        recorder = RunRecorder(self.config())
        case = scenarios(2, 1, 42)[0]
        table = build_table(recorder.game_config('index'), case, 'index', stage=2)
        class Index:
            busy = 0
            searches = 0
            async def call_tool(self, name, arguments, server='poker'):
                if name == 'search_experiences':
                    self.busy = 2
                    self.searches += 1
                    return {'status': 'indexing' if self.searches == 1 else 'ready', 'matches': [{'id': 'source'}]}
                self.busy = max(0, self.busy-1)
                return {'indexing': bool(self.busy), 'error': None}
        index = Index()
        await prepare_tools(table, index, recorder, 'index', {}, index=True, timeout=1)
        self.assertEqual(index.searches, 2)
        self.assertTrue(read_rows(recorder.root, 'contexts')[-1]['index_probe']['matches'])

    def test_invalid_condition_configuration_fails_before_any_trials(self):
        for conditions in [('both',), ('no_information', 'no_information')]:
            with self.assertRaises(ValueError):
                self.config(conditions=conditions)


if __name__ == '__main__':
    unittest.main()
