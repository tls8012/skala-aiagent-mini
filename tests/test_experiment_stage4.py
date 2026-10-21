"""실제 모델 없이 반사실 자료·입력 통제·엔진 접수·수동 평가 보존을 검사한다."""
from collections import defaultdict
from contextlib import redirect_stdout
import csv
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from agents.config import GameConfig
from agents.mcp_server import GameTools
from experiments.backends import SmokeClient
from experiments.common import ExperimentConfig, TRUST_CONDITIONS
from experiments.report import read_rows, summarize
from experiments.run import run
from experiments.stage4 import DEALS, answer_key, fixed_evidence, history_bank


class Stage4Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, {'LANGSMITH_TRACING': 'false', 'LANGCHAIN_TRACING_V2': 'false'})
        env.start()
        self.addCleanup(env.stop)

    async def execute(self, cases=2, **kwargs):
        with redirect_stdout(StringIO()):
            return await run(ExperimentConfig(stage=4, cases=cases, backend='fake', output=self.root, **kwargs))

    def test_completed_engine_histories_have_opposite_claim_truth_and_neutral_labels(self):
        banks, provenance = history_bank(GameConfig(data_dir=self.root))
        for condition, expected in [('trustworthy_history', 3), ('deceptive_history', 0)]:
            rows = banks[condition]
            self.assertEqual(len(rows), 3)
            self.assertEqual(len(provenance[condition]), 3)
            for row in rows:
                public = GameTools._public_record(row)
                self.assertEqual(public['events'][-1]['type'], 'RoundEnded')
                self.assertEqual(set(public['result']['revealed_cards']), {'A', 'B'})
                self.assertNotIn('AgentFailed', {e['type'] for e in public['events']})
            evidence = fixed_evidence(rows)
            truth = answer_key(evidence)
            self.assertEqual(truth['matching_claims'], expected)
            self.assertEqual(truth['nonmatching_claims'], 3-expected)
            self.assertTrue(all(c['speaker_rank'] == 9 for c in truth['claims']))
            self.assertNotIn(condition, json.dumps(evidence))
        self.assertEqual(fixed_evidence(banks['no_information']), {'status': 'skipped', 'matches': []})
        # 현재 가설 외에 과거의 발언·베팅·ID·상대 카드가 바뀌지 않는다.
        for before, after in zip(banks['trustworthy_history'], banks['deceptive_history']):
            self.assertEqual(before['events'][:-1], after['events'][:-1])
            self.assertEqual(before['result']['revealed_cards']['B'], after['result']['revealed_cards']['B'])

    async def test_only_evidence_changes_in_actual_prompts_and_every_action_uses_engine(self):
        root = await self.execute()
        trials = read_rows(root, 'trials')
        self.assertEqual(len(trials), 6)
        self.assertTrue(all(t['valid'] for t in trials))
        self.assertEqual(len({t['input_hash'] for t in trials}), 2)
        self.assertTrue(all(not t['memo_input_enabled'] and t['saved_memos']['round'] for t in trials))
        self.assertTrue(all(t['retrieval_mode'] == 'fixture' for t in trials))
        self.assertTrue(all(all(r['status'] == 'accepted' for r in t['applied']) for t in trials))
        self.assertTrue(all('chip_delta' not in t for t in trials))
        requests = [r for r in read_rows(root, 'model_calls') if r['event'] == 'request']
        self.assertEqual(len(requests), 6)
        inputs, systems, evidence_hashes = defaultdict(list), set(), defaultdict(set)
        by_trial = {t['trial_id']: t for t in trials}
        for request in requests:
            user = json.loads(request['user'])
            trial = by_trial[request['trial_id']]
            self.assertEqual(user['past_experiences'], fixed_evidence_from_disk(root, trial))
            self.assertNotIn('A', user['observation']['visible_cards'])
            self.assertEqual(user['observation']['visible_cards']['B'], trial['deal']['B'])
            self.assertTrue(user['force_bet'])
            user.pop('past_experiences')
            inputs[trial['case_id']].append(user)
            systems.add(request['system'])
            evidence_hashes[trial['condition']].add(trial['evidence_hash'])
        self.assertTrue(all(all(user == group[0] for user in group) for group in inputs.values()))
        self.assertEqual(len({json.dumps(group[0], sort_keys=True) for group in inputs.values()}), 2)
        self.assertEqual(len(systems), 1)
        self.assertTrue(all(len(hashes) == 1 for hashes in evidence_hashes.values()))
        self.assertEqual(len({next(iter(h)) for h in evidence_hashes.values()}), 3)
        self.assertFalse(any(t['name'] == 'search_experiences' for t in read_rows(root, 'tools')))
        manifest = json.loads((root / 'manifest.json').read_text())
        self.assertFalse(manifest['evidence_run'])
        self.assertEqual(manifest['planned_trials'], 6)
        self.assertEqual(manifest['protocol']['unit'], 'distinct_deal')
        self.assertEqual(manifest['protocol']['deal_count'], 2)
        summary = json.loads((root / 'summary.json').read_text())
        self.assertEqual(len(summary['paired']), 6)
        self.assertTrue(all(t['decision_pairs'] == 2 for t in summary['comparisons']))
        nodes = {t['name'] for t in read_rows(root, 'timings') if t['kind'] == 'node'}
        self.assertEqual(nodes, {'route', 'research', 'answer', 'validate', 'remember'})

    async def test_opposite_evidence_can_reach_different_accepted_actions_and_memos(self):
        def evidence_reader(client, system, user, schema=None):
            original, usage = original_complete(client, system, user, schema)
            payload = json.loads(original)
            matches = json.loads(user)['past_experiences']['matches']
            truthful = bool(matches) and all(m['round_context']['result']['revealed_cards']['A']['rank'] <= 10 for m in matches)
            payload['bet'] = {'action': 'fold' if truthful else 'call', 'raise_size': None}
            payload['memory_updates']['round'] = '일치 기록을 참고한 폴드 계획.' if truthful else '콜 계획.'
            return json.dumps(payload, ensure_ascii=False), usage
        original_complete = SmokeClient.complete
        with patch.object(SmokeClient, 'complete', evidence_reader):
            root = await self.execute()
        for trial in read_rows(root, 'trials'):
            self.assertTrue(trial['valid'])
            expected = 'fold' if trial['condition'] == 'trustworthy_history' else 'call'
            self.assertEqual(trial['betting_signature']['action'], expected)
            self.assertIn('폴드' if expected == 'fold' else '콜', trial['saved_memos']['round'])

    async def test_manual_stage4_review_is_blank_and_survives_report_regeneration(self):
        root = await self.execute(conditions=('trustworthy_history', 'deceptive_history'))
        path = root / 'stage4_review.csv'
        with path.open(newline='', encoding='utf-8') as handle:
            reader = csv.DictReader(handle)
            fields, rows = reader.fieldnames, list(reader)
        self.assertEqual(len(rows), 4)
        for row in rows:
            self.assertEqual(row['expected_claim_count'], '3')
            self.assertEqual(row['expected_matches'], '3' if row['condition'] == 'trustworthy_history' else '0')
            self.assertEqual(row['extraction_correct'], '')
        rows[0]['notes'] = '수동 검토: 자료 언급은 있지만 집계는 미표기'
        with path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        before = path.read_text()
        summarize(root)
        self.assertEqual(path.read_text(), before)
        summary = json.loads((root / 'summary.json').read_text())
        self.assertEqual(len(summary['paired']), 2)

    def test_stage4_rejects_other_stage_condition_names(self):
        self.assertEqual(ExperimentConfig(stage=4).arms, TRUST_CONDITIONS)
        self.assertEqual(ExperimentConfig(stage=4).cases, 10)
        self.assertEqual(ExperimentConfig(stage=3).cases, 30)
        with self.assertRaises(ValueError):
            ExperimentConfig(stage=4, cases=11)
        for condition in ('both', 'search_only', 'relevant_history'):
            with self.assertRaises(ValueError):
                ExperimentConfig(stage=4, conditions=(condition,))

    async def test_ten_distinct_deals_keep_history_fixed_and_pair_only_within_each_deal(self):
        root = await self.execute(cases=10)
        trials = read_rows(root, 'trials')
        self.assertEqual(len(trials), 30)
        self.assertTrue(all(t['valid'] for t in trials))
        self.assertEqual(len({t['input_hash'] for t in trials}), 10)
        self.assertEqual(len({json.dumps(t['deal']['B'], sort_keys=True) for t in trials}), 10)
        self.assertEqual(len({json.dumps(t['deal'], sort_keys=True) for t in trials}), 10)
        hashes, histories = defaultdict(set), defaultdict(set)
        for t in trials:
            hashes[t['case_id']].add(t['input_hash'])
            histories[t['condition']].add(t['evidence_hash'])
        self.assertTrue(all(len(h) == 1 for h in hashes.values()))
        self.assertTrue(all(len(h) == 1 for h in histories.values()))
        self.assertEqual(len(DEALS), 10)
        self.assertTrue(all(own != opponent for own, opponent in DEALS))
        self.assertTrue(any({own.rank, opponent.rank} == {2, 14} for own, opponent in DEALS))
        self.assertTrue(any(own.rank == opponent.rank for own, opponent in DEALS))
        for row in read_rows(root, 'model_calls'):
            if row['event'] == 'request':
                self.assertNotIn('A', json.loads(row['user'])['observation']['visible_cards'])
        summary = json.loads((root / 'summary.json').read_text())
        self.assertEqual(len(summary['paired']), 30)
        self.assertTrue(all(c['decision_pairs'] == 10 for c in summary['comparisons']))
        self.assertIn('배패별 행동', (root / 'summary.md').read_text())


def fixed_evidence_from_disk(root, trial):
    # 경험 저장 형식도 동일 결과를 전달했는지 별도로 대조한다.
    path = root / 'data' / trial['trial_id'] / 'experiences' / f"{trial['game_id']}.json"
    rows = json.loads(path.read_text())
    return fixed_evidence(rows)


if __name__ == '__main__':
    unittest.main()
