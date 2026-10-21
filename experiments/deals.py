"""3·4단계가 공유하는 평가용 배패. A의 실제 카드는 모델에 공개하지 않는다."""
from agents.cards import Card

# (비공개 A 카드, 관찰 가능한 상대 카드). 순서를 바꾸면 기존 사례 번호도 달라진다.
FIXED_DEALS = (
    (Card(10, 'H'), Card(9, 'C')),
    (Card(14, 'H'), Card(2, 'S')),
    (Card(2, 'C'), Card(14, 'H')),
    (Card(13, 'S'), Card(13, 'D')),
    (Card(9, 'S'), Card(10, 'C')),
    (Card(12, 'D'), Card(11, 'H')),
    (Card(11, 'H'), Card(12, 'S')),
    (Card(4, 'C'), Card(3, 'D')),
    (Card(5, 'S'), Card(5, 'H')),
    (Card(6, 'D'), Card(7, 'C')),
)


def deal_cards(case):
    own, opponent = FIXED_DEALS[case.index]
    return {'A': own, case.opponent: opponent}
