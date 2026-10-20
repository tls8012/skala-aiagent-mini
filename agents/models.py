"""외부/그래프 경계는 Pydantic, 내부 참가자는 dataclass."""
from dataclasses import dataclass
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


def safe_id(value: str) -> str:
    import re
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value):
        raise ValueError('ID must be 1..64 letters, digits, underscore or hyphen')
    return value


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


@dataclass
class Player:
    id: str
    kind: Literal['ai', 'human']
    chips: int
    debt: int = 0
    eliminated: bool = False
    failed: bool = False


class Bet(Contract):
    action: Literal['check', 'call', 'raise', 'fold']
    amount: int = Field(default=0, ge=0)

    @model_validator(mode='after')
    def validate_amount(self):
        if self.action == 'raise' and self.amount == 0:
            raise ValueError('raise amount is the positive increment above the current bet')
        if self.action != 'raise' and self.amount != 0:
            raise ValueError('only raise accepts amount')
        return self


class PlayerCommand(Contract):
    request_id: str = Field(min_length=1, max_length=128)
    round_id: int = Field(ge=1)
    basis_event: int = Field(ge=0)
    kind: Literal['speak', 'silent', 'bet']
    text: str = ''
    channel: Literal['game', 'chat'] = 'game'
    target: str | None = None
    reply_to: str | None = None
    bet: Bet | None = None

    @model_validator(mode='after')
    def validate_content(self):
        if self.kind == 'speak' and (not self.text.strip() or self.bet is not None):
            raise ValueError('speak requires nonempty text and no bet')
        if self.kind == 'bet' and (self.bet is None or self.text or self.channel != 'game' or self.target or self.reply_to):
            raise ValueError('bet requires only a bet')
        if self.kind == 'silent' and (self.bet is not None or self.text or self.target or self.reply_to or self.channel != 'game'):
            raise ValueError('silent cannot carry text, target or bet')
        return self


class AgentDecision(Contract):
    """사용자가 만들 그래프의 반환 계약. actor는 모델이 아닌 실행 코드가 지정한다."""
    round_id: int = Field(ge=1)
    basis_event: int = Field(ge=0)
    speech: Literal['speak', 'silent', 'unavailable']
    text: str = ''
    channel: Literal['game', 'chat'] = 'game'
    target: str | None = None
    reply_to: str | None = None
    bet: Bet | None = None
    wait_for_dialogue: bool = False

    @model_validator(mode='after')
    def validate_decision(self):
        if (self.speech == 'speak') != bool(self.text.strip()):
            raise ValueError('only speak may contain nonempty text')
        if self.speech != 'speak' and (self.channel != 'game' or self.target or self.reply_to):
            raise ValueError('only speak may carry chat routing fields')
        if self.channel == 'chat' and self.bet is not None:
            raise ValueError('chat reply must not carry a game bet')
        if self.bet is not None and self.wait_for_dialogue:
            raise ValueError('cannot bet and wait at once')
        return self


class Observation(Contract):
    game_id: str
    round_id: int
    player_id: str
    event_id: int
    phase: Literal['dialogue', 'revealed']
    players: list[dict[str, Any]]
    visible_cards: dict[str, dict[str, Any]]
    pot: int
    current_bet: int
    contributions: dict[str, int]
    betting_turn: str | None
    betting_open: bool
    allowed_bets: list[str]
    can_speak: bool
    required_game_utterances: int = 0
    last_speaker: str | None = None
    remaining_utterances: int
    dialogue_quiet: bool
    messages: list[dict[str, Any]]
    failed_agents: list[str]
    result: dict[str, Any] | None


class CommandResult(Contract):
    status: Literal['accepted', 'rejected', 'deferred']
    reason: str = ''
    event_id: int
    rejudge: bool = False
