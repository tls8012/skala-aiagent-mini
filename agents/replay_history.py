"""현재 테이블의 사건 직후 화면. RAG/디스크/SSE 버퍼와 별도로 메모리에 보관한다."""
from copy import deepcopy
from dataclasses import asdict
from uuid import uuid4


class ReplayHistory:
    def __init__(self, game_id: str, human_id: str | None):
        self.game_id = game_id
        self.human_id = human_id
        self._rounds: dict[int, list[dict]] = {}
        self._event_ids: dict[int, str] = {}
        self._display_events: dict[tuple[str, str], dict] = {}

    def capture(self, table, event: dict) -> str:
        """Table의 락 안에서 상태 반영 완료 후 호출한다. 중복 사건은 새 프레임을 만들지 않는다."""
        if event['event_id'] in self._event_ids:
            return self._event_ids[event['event_id']]
        r = table.round
        if event['type'] == 'RoundStarted':
            self._display_events.clear()
        if event['type'] in {'Speech', 'BetPlaced'}:
            self._display_events[(event['type'], event['player_id'])] = deepcopy(event)
        has_human_observation = self.human_id is not None and self.human_id in r.order
        visible = (table.observe(self.human_id).visible_cards if has_human_observation
                   else r.result['revealed_cards'] if r.result else {})
        human = table.players.get(self.human_id)
        snapshot = {
            'game_id': self.game_id, 'round_id': r.number, 'event_id': event['event_id'],
            'phase': r.phase, 'players': [asdict(player) for player in table.players.values()],
            'human_id': self.human_id, 'folded': sorted(r.betting.folded),
            'pot': r.pot, 'current_bet': r.betting.current_bet, 'betting_turn': r.betting.turn,
            'visible_cards': visible, 'result': r.result, 'game_winner': table.game_winner,
            'finished': table.finished, 'perspective': 'player' if has_human_observation else 'spectator',
            'human_display': {
                'participation': 'playing' if human and not human.eliminated else 'spectating',
                'remaining_utterances': max(0, table.config.max_utterances - r.conversation.counts.get(self.human_id, 0))
                if has_human_observation else 0,
            },
            'display_events': sorted(self._display_events.values(), key=lambda item: item['event_id']),
        }
        frame_id = uuid4().hex
        frame = deepcopy({'frame_id': frame_id, 'game_id': self.game_id, 'round_id': r.number,
                          'event_id': event['event_id'], 'event': event, 'snapshot': snapshot})
        self._rounds.setdefault(r.number, []).append(frame)
        self._event_ids[event['event_id']] = frame_id
        return frame_id

    def decorate(self, event: dict) -> dict:
        """서비스 전송 복사본에만 링크를 붙인다. 원본 공개/RAG 기록은 바꾸지 않는다."""
        result = deepcopy(event)
        frame_id = self._event_ids.get(event.get('event_id'))
        if frame_id:
            result['frame_id'] = frame_id
        return result

    def manifest(self) -> dict:
        rounds = [{'round_id': number, 'completed': frames[-1]['event']['type'] == 'RoundEnded',
                   'frame_count': len(frames), 'first_frame_id': frames[0]['frame_id'],
                   'last_frame_id': frames[-1]['frame_id']} for number, frames in self._rounds.items()]
        return {'game_id': self.game_id, 'rounds': rounds,
                'latest_frame_id': rounds[-1]['last_frame_id'] if rounds else None}

    def get_round(self, number: int) -> dict:
        frames = self._rounds[number]
        return deepcopy({'game_id': self.game_id, 'round_id': number,
                         'completed': frames[-1]['event']['type'] == 'RoundEnded',
                         'latest_frame_id': frames[-1]['frame_id'], 'frames': frames})

    def clear(self):
        self._rounds.clear()
        self._event_ids.clear()
        self._display_events.clear()
