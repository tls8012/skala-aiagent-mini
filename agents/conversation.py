"""발언 제한과 보류함. 공개 메시지에만 순서를 부여한다."""
from .config import GameConfig
from .models import PlayerCommand, SpeechInterpretation


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
        self.chat_replies: dict[str, set[str]] = {}
        self.chat_senders: dict[str, str] = {}
        self.mixed_requests: set[str] = set()
        self.deferred_interpretations: dict[str, SpeechInterpretation] = {}

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

    def add(self, pid: str, command: PlayerCommand, event_id: int,
            interpretation: SpeechInterpretation | None = None):
        if self.players[pid].kind == 'human' or command.channel in {'game', 'mixed'}:
            self.counts[pid] += 1
        if command.channel in {'game', 'mixed'}:
            self.game_counts[pid] += 1
            self.silent.clear()
        self.silent.discard(pid)
        self.last_speaker = pid
        self.messages.append({'event_id': event_id, 'request_id': command.request_id,
                              'player_id': pid, 'channel': command.channel,
                              'target': command.target, 'text': command.text})
        if interpretation:
            self.messages[-1]['interpretation'] = interpretation.model_dump()
        if self.players[pid].kind == 'human':
            recipients = ({other for intent in interpretation.intents if intent.kind != 'game'
                           for other in intent.targets} if interpretation else
                          {command.target} if command.channel == 'chat' else set())
            if recipients:
                self.chat_replies[command.request_id] = recipients
                self.chat_senders[command.request_id] = pid
                if interpretation and interpretation.has_game:
                    self.mixed_requests.add(command.request_id)

    def context(self) -> list[dict]:
        return self.messages[-self.config.context_message_limit:]
