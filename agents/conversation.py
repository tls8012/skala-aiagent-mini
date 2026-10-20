"""발언 제한과 보류함. 공개 메시지에만 순서를 부여한다."""
from .config import GameConfig
from .models import PlayerCommand


class Conversation:
    def __init__(self, players: dict, config: GameConfig):
        self.players = players
        self.config = config
        self.counts = dict.fromkeys(players, 0)
        self.game_counts = dict.fromkeys(players, 0)
        self.silent: set[str] = set()
        self.last_speaker: str | None = None
        self.messages: list[dict] = []
        self.deferred: dict[str, PlayerCommand] = {}
        self.chat_replies: dict[str, str] = {}  # human request ID -> addressed AI

    def exhausted(self, pid: str) -> bool:
        return self.counts[pid] >= self.config.max_utterances

    def minimum_met(self, active: list[str]) -> bool:
        return all(self.players[pid].failed or self.game_counts[pid] >= self.config.min_utterances
                   for pid in active)

    def quiet(self, active: list[str]) -> bool:
        return all(self.players[pid].failed or self.exhausted(pid) or pid in self.silent
                   for pid in active)

    def speech_allowed(self, pid: str, channel: str) -> bool:
        return self.players[pid].kind == 'ai' and channel == 'chat' or not self.exhausted(pid)

    def add(self, pid: str, command: PlayerCommand, event_id: int):
        if self.players[pid].kind == 'human' or command.channel == 'game':
            self.counts[pid] += 1
        if command.channel == 'game':
            self.game_counts[pid] += 1
            self.silent.clear()
        self.silent.discard(pid)
        self.last_speaker = pid
        self.messages.append({'event_id': event_id, 'request_id': command.request_id,
                              'player_id': pid, 'channel': command.channel,
                              'target': command.target, 'text': command.text})
        if command.channel == 'chat' and self.players[pid].kind == 'human':
            self.chat_replies[command.request_id] = command.target

    def context(self) -> list[dict]:
        return self.messages[-self.config.context_message_limit:]
