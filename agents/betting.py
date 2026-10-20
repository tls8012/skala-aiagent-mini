"""한 번의 베팅 순환. amount는 현재 최고액에 추가하는 레이즈 증가량."""
from .models import Bet, Player


class Betting:
    def __init__(self, order: list[str], players: dict[str, Player], ante: int, min_raise: int):
        self.order = list(order)
        self.players = players
        self.min_raise = min_raise
        self.folded: set[str] = set()
        self.contributions = {pid: 0 for pid in order}
        self.current_bet = 0
        self.pot = 0
        self.pending = set(order)
        self.turn: str | None = order[0]
        for pid in order:
            self._pay(pid, ante)

    @property
    def alive(self) -> list[str]:
        return [pid for pid in self.order if pid not in self.folded]

    @property
    def complete(self) -> bool:
        return len(self.alive) <= 1 or not self.pending

    def _pay(self, pid: str, amount: int):
        player = self.players[pid]
        shortage = max(0, amount - player.chips)
        player.debt += shortage
        player.chips += shortage
        player.chips -= amount
        self.pot += amount

    def to_call(self, pid: str) -> int:
        return self.current_bet - self.contributions[pid]

    def allowed(self, pid: str) -> list[str]:
        if self.complete or pid != self.turn:
            return []
        return ['check' if self.to_call(pid) == 0 else 'call', 'raise', 'fold']

    def act(self, pid: str, bet: Bet):
        if self.complete or pid != self.turn:
            raise ValueError('not your betting turn')
        if bet.action not in self.allowed(pid):
            raise ValueError('cannot check while a call is required')
        if bet.action == 'raise' and bet.amount < self.min_raise:
            raise ValueError('raise increment is too small')
        if bet.action == 'fold':
            self.folded.add(pid)
        elif bet.action == 'raise':
            self.current_bet += bet.amount
            self.pending = set(self.alive) - {pid}
            amount = self.to_call(pid)
            self._pay(pid, amount)
            self.contributions[pid] += amount
        elif bet.action == 'call':
            amount = self.to_call(pid)
            self._pay(pid, amount)
            self.contributions[pid] += amount
        self.pending.discard(pid)
        if self.complete:
            self.turn = None
            return
        pos = self.order.index(pid)
        self.turn = next(self.order[(pos + offset) % len(self.order)]
                         for offset in range(1, len(self.order) + 1)
                         if self.order[(pos + offset) % len(self.order)] in self.pending)
