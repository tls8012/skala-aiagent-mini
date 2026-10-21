"""새 상대의 경계 규칙·결정성·숨은 카드 격리와 실제 엔진 접수를 검증한다."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from agents.cards import Card
from agents.config import GameConfig
from agents.models import Bet
from agents.table import Table
from experiments.fixtures import Scenario, command, history_bank
from experiments.rule_opponents import BRulePolicy, CRulePolicy, POLICY_VERSIONS


class RuleOpponentTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.config = GameConfig(data_dir=Path(folder.name))
        self.number = 0

    def scene(self, pid, rank, cost=0, *, chips=30, own=Card(9, 'H')):
        self.number += 1
        table = Table(['A', pid] if cost else [pid, 'A'],
                      config=replace(self.config, initial_chips=chips), game_id=f'scene-{self.number}')
        table.start_round({'A': Card(rank, 'S'), pid: own})
        command(table, 'A', 'speak', text='판단할게.', request_id='speech-A')
        command(table, pid, 'speak', text='주장을 말할게.', request_id='speech-opponent')
        if cost:
            size = {1: 'small', 2: 'medium', 4: 'large', 28: 'maximum'}[cost]
            command(table, 'A', 'bet', bet=Bet(action='raise', raise_size=size))
        return table

    def test_b_boundaries_and_costs(self):
        cases = [(7, 0, 'raise', 'large'), (8, 0, 'raise', 'medium'),
                 (10, 0, 'raise', 'medium'), (11, 0, 'check', None),
                 (14, 1, 'call', None), (11, 2, 'fold', None),
                 (10, 4, 'call', None), (10, 28, 'fold', None),
                 (7, 2, 'raise', 'large'), (7, 28, 'call', None)]
        for rank, cost, action, size in cases:
            with self.subTest(rank=rank, cost=cost):
                table = self.scene('B', rank, cost)
                decision = BRulePolicy().decide(table.observe('B'))
                self.assertEqual(decision.bet, Bet(action=action, raise_size=size))
                self.assertTrue(all(r.status=='accepted' for r in table.apply_decision('B', decision, 'apply')))

    def test_c_boundaries_and_costs(self):
        cases = [(7, 0, 'raise', 'small'), (8, 0, 'check', None),
                 (7, 28, 'call', None), (8, 2, 'call', None),
                 (10, 2, 'call', None), (8, 4, 'fold', None),
                 (11, 1, 'fold', None), (14, 0, 'check', None)]
        for rank, cost, action, size in cases:
            with self.subTest(rank=rank, cost=cost):
                table = self.scene('C', rank, cost)
                decision = CRulePolicy().decide(table.observe('C'))
                self.assertEqual(decision.bet, Bet(action=action, raise_size=size))
                self.assertTrue(all(r.status=='accepted' for r in table.apply_decision('C', decision, 'apply')))

    def test_claim_truth_is_opposite_for_b_and_truthful_for_c(self):
        for rank in range(2, 15):
            table = self.scene('B', rank)
            obs = table.observe('B')
            self.assertEqual(BRulePolicy().claim(obs), '네 카드는 J 이상이야.' if rank<=10 else '네 카드는 10 이하야.')
            self.assertEqual(CRulePolicy().claim(obs), '네 카드는 10 이하야.' if rank<=10 else '네 카드는 J 이상이야.')

    def test_one_accepted_raise_limit_and_duplicate_event_handling(self):
        for pid, cls in [('B', BRulePolicy), ('C', CRulePolicy)]:
            table = self.scene(pid, 5)
            policy = cls()
            before = table.event_id
            table.apply_decision(pid, policy.decide(table.observe(pid)), 'first')
            for event in table.events_since(before):
                policy.on_event(event)
                policy.on_event(event)
            self.assertEqual(policy.raises[1], 1)
            command(table, 'A', 'bet', bet=Bet(action='raise', raise_size='small'))
            self.assertEqual(policy.decide(table.observe(pid)).bet.action, 'call')

    def test_same_observation_needs_no_rng_and_cannot_see_own_card(self):
        for pid, cls in [('B', BRulePolicy), ('C', CRulePolicy)]:
            left = self.scene(pid, 7, own=Card(9, 'H')).observe(pid)
            right = self.scene(pid, 7, own=Card(12, 'D')).observe(pid)
            self.assertNotIn(pid, left.visible_cards)
            a, b = left.model_dump(), right.model_dump()
            a.pop('game_id'); b.pop('game_id')
            self.assertEqual(a, b)
            policy = cls()
            with patch('random.Random', side_effect=AssertionError('must not use RNG')):
                one, two = policy.decide(left), policy.decide(left)
                other = cls().decide(right)
            self.assertEqual(one, two)
            self.assertEqual(one, other)

    def test_raise_size_falls_back_to_engine_options(self):
        for chips, expected in [(5, 'medium'), (3, 'small'), (2, None)]:
            table = self.scene('B', 5, chips=chips)
            decision = BRulePolicy().decide(table.observe('B'))
            self.assertEqual(decision.bet.raise_size, expected)
            self.assertTrue(all(r.status=='accepted' for r in table.apply_decision('B', decision, 'apply')))

    def test_stage3_histories_use_new_policies_and_public_claims_match_their_rules(self):
        for pid in ('B', 'C'):
            case = Scenario(f'bank-{pid}', 'FIXED_DEAL', 30042, pid, 0)
            config = replace(self.config, data_dir=self.config.data_dir / pid)
            rows, provenance = history_bank(config, case, stage=3, deterministic_ids=True)
            self.assertEqual(len(rows), 20)
            self.assertTrue(all(p['policy_version']==POLICY_VERSIONS[pid] for p in provenance))
            for row in rows:
                rank = row['result']['revealed_cards']['A']['rank']
                speech = next(e['text'] for e in row['events'] if e['type']=='Speech' and e['player_id']==pid)
                truth = (rank<=10) if '10 이하' in speech else (rank>=11)
                self.assertEqual(truth, pid=='C')


if __name__ == '__main__':
    unittest.main()
