"""현재 게임 리플레이: 사건 경계, 불변성, 관점/카드 공개 범위, 보관 기간."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from agents import GameConfig, Table
from agents.cards import Card
from agents.models import Bet, PlayerCommand


class ReplayHistoryTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.config = GameConfig(data_dir=Path(folder.name), target_chips=999, event_history_limit=2)

    def table(self, human=True, **settings):
        return Table(['A', 'B'], 'human' if human else None, replace(self.config, **settings))

    def command(self, table, pid, kind, **kwargs):
        return table.submit(pid, PlayerCommand(request_id=uuid4().hex, round_id=table.round.number,
                            basis_event=table.event_id, kind=kind, **kwargs))

    def speak_all(self, table):
        for pid in table.round.order:
            self.assertEqual(self.command(table, pid, 'speak', text=pid + '의 발언').status, 'accepted')

    def finish(self, table):
        while table.round.phase == 'dialogue':
            self.assertEqual(self.command(table, table.round.betting.turn, 'bet', bet=Bet(action='check')).status, 'accepted')

    def test_speech_frame_is_after_count_and_latest_bubble_update(self):
        table = self.table()
        self.assertEqual(table.replay_history()['rounds'], [])
        table.start_round()
        self.command(table, 'human', 'speak', text='처음 발언')
        frame = table.replay_history(1)['frames'][-1]
        self.assertEqual(frame['event']['text'], '처음 발언')
        self.assertEqual(frame['snapshot']['human_display']['remaining_utterances'], 4)
        self.assertEqual(frame['snapshot']['display_events'][-1], frame['event'])
        self.assertEqual(frame['event_id'], frame['snapshot']['event_id'])
        self.assertEqual(frame['snapshot']['perspective'], 'player')
        self.assertEqual(set(frame['snapshot']['visible_cards']), {'A', 'B'})

    def test_deferred_rejected_repeated_commands_have_no_extra_frame(self):
        table = self.table()
        table.start_round()
        first = PlayerCommand(request_id='once', round_id=1, basis_event=table.event_id,
                              kind='speak', text='첫말')
        table.submit('A', first)
        baseline = table.replay_history(1)
        table.submit('A', first)
        self.assertEqual(self.command(table, 'A', 'speak', text='보류').status, 'deferred')
        self.assertEqual(self.command(table, 'B', 'bet', bet=Bet(action='check')).status, 'rejected')
        self.assertEqual(table.replay_history(1), baseline)
        self.command(table, 'B', 'speak', text='끼어들기')
        table.release_deferred('A', table.event_id)
        frames = table.replay_history(1)['frames']
        self.assertEqual(frames[-1]['event']['text'], '보류')
        self.assertEqual(frames[-1]['snapshot']['human_display']['remaining_utterances'], 5)
        event = table.events[-1]
        self.assertEqual(table.history.capture(table, event), frames[-1]['frame_id'])
        self.assertEqual(len(table.replay_history(1)['frames']), len(frames))

    def test_last_bet_and_settlement_have_separate_exact_snapshots(self):
        table = self.table(initial_chips=2, target_chips=5)
        table.start_round({'A': Card(13, 'S'), 'B': Card(8, 'H'), 'human': Card(3, 'C')})
        self.speak_all(table)
        self.finish(table)
        bet, end = table.replay_history(1)['frames'][-2:]
        self.assertEqual(bet['event']['type'], 'BetPlaced')
        self.assertEqual(bet['snapshot']['phase'], 'dialogue')
        self.assertIsNone(bet['snapshot']['result'])
        self.assertEqual([p['chips'] for p in bet['snapshot']['players']], [0, 0, 0])
        self.assertEqual(end['event']['type'], 'RoundEnded')
        self.assertEqual(end['snapshot']['phase'], 'revealed')
        self.assertEqual([p['chips'] for p in end['snapshot']['players']], [6, 0, 0])
        self.assertTrue(end['snapshot']['finished'])
        self.assertEqual(end['snapshot']['game_winner'], 'A')
        self.assertTrue(table.replay_history()['rounds'][0]['completed'])

    def test_folded_cards_and_future_reveals_never_backfill_old_frames(self):
        table = self.table()
        table.start_round()
        first = table.replay_history(1)['frames'][0]
        self.speak_all(table)
        self.command(table, 'A', 'bet', bet=Bet(action='fold'))
        folded = table.replay_history(1)['frames'][-1]
        self.assertNotIn('A', folded['snapshot']['visible_cards'])
        self.command(table, 'B', 'bet', bet=Bet(action='check'))
        self.command(table, 'human', 'bet', bet=Bet(action='check'))
        end = table.replay_history(1)['frames'][-1]
        self.assertEqual(set(end['snapshot']['visible_cards']), {'B', 'human'})
        self.assertEqual(table.replay_history(1)['frames'][0], first)
        self.assertNotIn('human', first['snapshot']['visible_cards'])
        self.assertNotIn('A', end['snapshot']['result']['revealed_cards'])

    def test_old_round_survives_event_buffer_rotation_and_read_mutations(self):
        table = self.table(human=False)
        table.start_round()
        self.speak_all(table)
        self.finish(table)
        saved = table.replay_history(1)
        table.start_round()
        self.assertTrue(all(e['round_id'] == 2 for e in table.events))
        self.assertEqual(table.replay_history(1), saved)
        modified = table.replay_history(1)
        modified['frames'][0]['snapshot']['players'][0]['chips'] = -999
        modified['frames'][-1]['snapshot']['result']['revealed_cards'].clear()
        modified['frames'][1]['event']['text'] = '변조'
        self.assertEqual(table.replay_history(1), saved)
        self.assertEqual([r['round_id'] for r in table.replay_history()['rounds']], [1, 2])
        self.assertEqual(table.replay_history(2)['frames'][0]['snapshot']['display_events'], [])
        archived = json.loads(next(table.archive_dir.glob('*.json')).read_text())
        self.assertNotIn('frame_id', json.dumps(archived))
        self.assertNotIn('snapshot', json.dumps(archived))
        self.assertTrue(all('frame_id' not in e for e in table.events))

    def test_ai_only_hides_cards_until_reveal_and_failed_action_has_own_frame(self):
        table = self.table(human=False)
        table.start_round()
        first = table.replay_history(1)['frames'][0]
        self.assertEqual(first['snapshot']['visible_cards'], {})
        self.assertEqual(first['snapshot']['perspective'], 'spectator')
        self.command(table, 'B', 'speak', text='시작')
        table.fail_agent('A')
        frames = table.replay_history(1)['frames']
        failure, automatic = frames[-2:]
        self.assertEqual(failure['event']['type'], 'AgentFailed')
        self.assertEqual(failure['snapshot']['betting_turn'], 'A')
        self.assertTrue(failure['snapshot']['players'][0]['failed'])
        self.assertTrue(automatic['event']['automatic'])
        self.assertEqual(automatic['snapshot']['betting_turn'], 'B')
        self.finish(table)
        self.assertEqual(set(table.replay_history(1)['frames'][-1]['snapshot']['visible_cards']), {'A', 'B'})
        self.assertEqual(table.replay_history(1)['frames'][0], first)
        table.clear_replay_history()
        self.assertEqual(table.replay_history()['rounds'], [])
        with self.assertRaises(KeyError):
            table.replay_history(1)
