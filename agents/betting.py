"""대출 없는 베팅. 크기 이름을 서버에서 레이즈 증가량으로 변환한다."""
from .models import Bet, Player


class Betting:
    def __init__(self, order: list[str], players: dict[str, Player], ante: int, min_raise: int):
        if any(players[pid].chips < ante for pid in order):
            raise ValueError('not enough chips for ante')
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
        if amount < 0 or amount > player.chips:
            raise ValueError('not enough chips')
        player.chips -= amount
        self.pot += amount

    def to_call(self, pid: str) -> int:
        return self.current_bet - self.contributions[pid]

    def raise_options(self, pid: str) -> dict[str, int]:
        if self.complete or pid != self.turn:
            return {}
        # 각 참가자가 아직 내야 할 콜을 제외한다. 가장 적은 잔액까지를
        # 한도로 삼아 이후 전원이 콜할 수 있게 한다(올인/사이드팟 없음).
        cap = min(self.players[other].chips - self.to_call(other) for other in self.alive)
        if cap < self.min_raise:
            return {}
        return {name: amount for name, amount in
                [('maximum', cap), ('large', 4), ('medium', 2), ('small', 1)]
                if self.min_raise <= amount <= cap}

    def allowed(self, pid: str) -> list[str]:
        if self.complete or pid != self.turn:
            return []
        actions = ['check' if self.to_call(pid) == 0 else 'call']
        if self.raise_options(pid):
            actions.append('raise')
        return actions + ['fold']

    def act(self, pid: str, bet: Bet) -> int:
        """적용된 레이즈 증가량을 반환한다. 거부 시 상태를 바꾸지 않는다."""
        if self.complete or pid != self.turn:
            raise ValueError('not your betting turn')
        if bet.action not in self.allowed(pid):
            raise ValueError('bet action is not allowed')
        increment = 0
        if bet.action == 'raise':
            options = self.raise_options(pid)
            if bet.raise_size not in options:
                raise ValueError('raise size is not available')
            increment = options[bet.raise_size]
        if bet.action == 'fold':
            self.folded.add(pid)
        elif bet.action == 'raise':
            self.current_bet += increment
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
            return increment
        pos = self.order.index(pid)
        self.turn = next(self.order[(pos + offset) % len(self.order)]
                         for offset in range(1, len(self.order) + 1)
                         if self.order[(pos + offset) % len(self.order)] in self.pending)
        return increment
