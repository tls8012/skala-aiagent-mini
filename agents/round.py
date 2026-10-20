"""한 라운드의 규칙 객체. 최종 기록 저장과 참가자 실행은 Table 담당."""
from .betting import Betting
from .cards import Card, find_winner
from .config import GameConfig
from .conversation import Conversation
from .models import Player


class Round:
    def __init__(self, number: int, order: list[str], players: dict[str, Player],
                 cards: dict[str, Card], carry_pot: int, config: GameConfig):
        self.number = number
        self.order = order
        self.players = players
        self.cards = cards
        self.phase = 'dialogue'
        self.betting = Betting(order, players, config.ante, config.min_raise)
        self.conversation = Conversation({pid: players[pid] for pid in order}, config)
        self.carry_pot = carry_pot
        self.result: dict | None = None

    @property
    def pot(self) -> int:
        return self.carry_pot + self.betting.pot

    @property
    def betting_open(self) -> bool:
        return self.conversation.minimum_met(self.betting.alive)

    def settle(self) -> dict:
        if self.result is not None:
            return self.result
        if not self.betting.complete:
            raise ValueError('betting is not complete')
        contenders = {pid: self.cards[pid] for pid in self.betting.alive}
        winner = find_winner(contenders)
        pot = self.pot
        if winner:
            self.players[winner].chips += pot
        eliminated = []
        for pid in self.order:
            player = self.players[pid]
            repayment = min(player.chips, player.debt)
            player.chips -= repayment
            player.debt -= repayment
            if player.chips <= 0:
                player.eliminated = True
                eliminated.append(pid)
        self.phase = 'revealed'
        self.result = {'winner': winner, 'draw': winner is None, 'pot': pot,
                       'carry_pot': pot if winner is None else 0,
                       'revealed_cards': {pid: card.to_dict() for pid, card in contenders.items()},
                       'eliminated': eliminated}
        self.conversation.deferred.clear()
        return self.result
