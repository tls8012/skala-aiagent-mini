// 화면 개발 전용 예시. 서버 기록/리플레이 프레임으로 사용하지 않는다.
const speech = (eventId, playerId, text) => ({ eventId, roundId: 1, playerId, channel: 'game', text });
const a = speech(2, 'A', 'B가 너무 조용한데? 좋은 카드라도 들고 있는 거야?');
const b = speech(3, 'B', '조용하다고 좋은 카드라는 법은 없지. 다들 너무 나만 보는 거 아니야?');
const c = speech(5, 'C', '난 폴드했지만 말은 할 수 있지. B야, 방금 왜 웃었어?');
const human = speech(6, 'human', 'A, C야. 이번에는 B를 같이 견제해 보자. 나는 B의 반응을 좀 더 보고 결정하고 싶어. 방금 조용해진 것도 조금 수상하잖아. 물론 너희가 다른 생각이 있다면 먼저 들어볼게.');

export const previewView = {
  aiSeats: [
    { key: 'preview-a', playerId: 'A', label: 'A', color: '#ee7976', gazeAngle: 180, position: 'right', state: 'active', chips: 28, card: { visibility: 'face', rank: 14, suit: 'S' }, speech: a, lastActionLabel: '체크' },
    { key: 'preview-b', playerId: 'B', label: 'B', color: '#6b9edb', gazeAngle: 90, position: 'top', state: 'active', chips: 28, isBettingTurn: true, card: { visibility: 'face', rank: 12, suit: 'H' }, speech: b },
    { key: 'preview-c', playerId: 'C', label: 'C', color: '#a68bce', gazeAngle: 0, position: 'left', state: 'folded', chips: 28, card: { visibility: 'back' }, speech: c, lastActionLabel: '폴드' },
  ],
  humanSeat: { key: 'human-anchor', playerId: 'human', label: '나', color: '#111114', gazeAngle: -90, role: 'human', participation: 'playing', state: 'active', chips: 28, card: { visibility: 'back' }, speech: human },
  summary: { roundId: 1, phase: 'dialogue', pot: 8, currentBet: 0, bettingTurn: 'B' },
};

export const previewEntries = [
  { id: 'preview-1', roundId: 1, eventId: 1, type: 'RoundStarted', text: '카드를 나눴습니다. 대화를 시작합니다.' },
  ...[a, b].map(item => ({ id: `preview-${item.eventId}`, roundId: 1, eventId: item.eventId, type: 'Speech', playerId: item.playerId, text: item.text })),
  { id: 'preview-4', roundId: 1, eventId: 4, type: 'BetPlaced', playerId: 'C', text: '폴드했습니다. 카드는 공개하지 않습니다.' },
  ...[c, human].map(item => ({ id: `preview-${item.eventId}`, roundId: 1, eventId: item.eventId, type: 'Speech', playerId: item.playerId, text: item.text })),
];
