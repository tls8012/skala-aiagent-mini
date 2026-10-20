"""52장 덱과 순환 서열. rank는 2..14(A), suit는 C/H/D/S."""
from dataclasses import dataclass
import random

SUITS = {'C': 0, 'H': 1, 'D': 2, 'S': 3}


@dataclass(frozen=True)
class Card:
    rank: int
    suit: str

    def __post_init__(self):
        if type(self.rank) is not int or self.rank not in range(2, 15) or self.suit not in SUITS:
            raise ValueError('invalid card')

    def to_dict(self) -> dict:
        return {'rank': self.rank, 'suit': self.suit}

    def beats(self, other: 'Card') -> bool:
        if self.rank == other.rank:
            return SUITS[self.suit] > SUITS[other.suit]
        if {self.rank, other.rank} == {2, 14}:
            return self.rank == 2
        return self.rank > other.rank


class Deck:
    def __init__(self, rng: random.Random | None = None):
        self.cards = [Card(rank, suit) for rank in range(2, 15) for suit in SUITS]
        (rng or random.Random()).shuffle(self.cards)

    def deal(self, count: int) -> list[Card]:
        if not 0 <= count <= len(self.cards):
            raise ValueError('not enough cards')
        return [self.cards.pop() for _ in range(count)]


def find_winner(cards: dict[str, Card]) -> str | None:
    """모든 상대를 이기는 카드만 승리. 없으면 순환 무승부."""
    return next((pid for pid, card in cards.items()
                 if all(pid == other or card.beats(value) for other, value in cards.items())), None)
