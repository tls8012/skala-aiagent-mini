"""고정 배패/과거 자료와 실제 라운드 성과 집계를 모델 호출 없이 검증한다."""
from collections import defaultdict
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from experiments.common import ExperimentConfig
from experiments.deals import FIXED_DEALS
from experiments.report import read_rows, summarize
from experiments.run import run


class Stage3DealTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, {'LANGSMITH_TRACING': 'false', 'LANGCHAIN_TRACING_V2': 'false'})
        env.start()
        self.addCleanup(env.stop)

    async def execute(self, **kwargs):
        config = ExperimentConfig(stage=3, backend='fake', output=self.root, **kwargs)
        with redirect_stdout(StringIO()):
            return await run(config)

    async def test_ten_deals_finish_forty_rounds_with_paired_inputs_and_fixed_banks(self):
        root = await self.execute()
        trials = read_rows(root, 'trials')
        self.assertEqual(len(trials), 40)
        self.assertTrue(all(t['valid'] for t in trials))
        hashes, banks, public_banks, memos = defaultdict(set), defaultdict(set), defaultdict(set), defaultdict(set)
        game_ids = defaultdict(set)
        for trial in trials:
            hashes[trial['case_id']].add(trial['input_hash'])
            game_ids[trial['case_id']].add(trial['game_id'])
            banks[trial['opponent']].add(trial['bank_hash'])
            public_banks[trial['opponent']].add(trial['public_bank_hash'])
            memos[trial['opponent'], trial['condition']].add(trial['memo_input_hash'])
            index = trial['scenario']['index']
            own, opponent = FIXED_DEALS[index]
            self.assertEqual(trial['deal'], {'A': own.to_dict(), trial['opponent']: opponent.to_dict()})
            self.assertEqual(trial['final_chips']-trial['initial_chips'], trial['chip_delta'])
            self.assertFalse(trial['runner_errors'])
        self.assertEqual(len(hashes), 20)
        self.assertTrue(all(len(values) == 1 for collection in (hashes, banks, public_banks, memos, game_ids) for values in collection.values()))
        manifest = json.loads((root / 'manifest.json').read_text())
        self.assertEqual(manifest['planned_trials'], 40)
        self.assertEqual(manifest['protocol']['deals_per_opponent'], 10)
        self.assertEqual(set(manifest['history_banks']), {'B', 'C'})
        self.assertTrue(all(bank['rounds'] == 20 for bank in manifest['history_banks'].values()))
        by_id = {t['trial_id']: t for t in trials}
        for call in read_rows(root, 'model_calls'):
            if call['event'] == 'request':
                observation = json.loads(call['user'])['observation']
                if observation['phase'] != 'revealed':
                    self.assertNotIn('A', observation['visible_cards'])
                    trial = by_id[call['trial_id']]
                    self.assertEqual(observation['visible_cards'][trial['opponent']], trial['deal'][trial['opponent']])
        summary = json.loads((root / 'summary.json').read_text())
        for group in summary['conditions']:
            rows = [t for t in trials if t['condition'] == group['condition'] and t['opponent'] == group['opponent']]
            self.assertEqual(group['completed_rounds_valid'], 10)
            self.assertEqual(group['win_rate_valid'], sum(t['winner']=='A' for t in rows)/10)
            self.assertEqual(group['mean_chip_delta_valid'], sum(t['chip_delta'] for t in rows)/10)
        self.assertTrue(all(c['round_pairs'] == 10 for c in summary['comparisons']))
        self.assertIn('배패별 승패와 손익', (root / 'summary.md').read_text())

    async def test_history_is_identical_across_runs_even_when_evaluation_seed_changes(self):
        first = await self.execute(cases=1, seed=42)
        second = await self.execute(cases=1, seed=73)
        def source_hashes(root):
            return {(t['opponent'], t['condition']): (t['bank_hash'], t['public_bank_hash'], t['memo_input_hash'])
                    for t in read_rows(root, 'trials')}
        self.assertEqual(source_hashes(first), source_hashes(second))

    async def test_invalid_completed_round_is_excluded_from_win_rate_and_valid_pairs(self):
        root = await self.execute(cases=1)
        trials = read_rows(root, 'trials')
        invalid = trials[0]
        invalid['valid'] = False
        invalid['error'] = 'simulated late audit failure'
        (root / 'trials.jsonl').write_text(''.join(json.dumps(t, ensure_ascii=False)+'\n' for t in trials))
        summary = summarize(root)
        group = next(g for g in summary['conditions'] if g['condition'] == invalid['condition'] and g['opponent'] == invalid['opponent'])
        self.assertEqual(group['completed_rounds'], 1)
        self.assertEqual(group['completed_rounds_valid'], 0)
        self.assertIsNone(group['win_rate_valid'])
        pair = next(c for c in summary['comparisons'] if c['opponent'] == invalid['opponent'])
        self.assertEqual(pair['round_pairs'], 0)
        self.assertEqual(pair['valid_pairs'], 0)

    def test_default_and_case_limit(self):
        self.assertEqual(ExperimentConfig(stage=3).cases, 10)
        self.assertEqual(ExperimentConfig(stage=1).cases, 30)
        self.assertEqual(ExperimentConfig(stage=2).cases, 30)
        with self.assertRaises(ValueError):
            ExperimentConfig(stage=3, cases=11)


if __name__ == '__main__':
    unittest.main()
