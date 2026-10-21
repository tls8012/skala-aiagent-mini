/** 공개 사건의 중복 제거와 화면 기록 변환. 전송 번호와 게임 사건 번호는 분리한다. */
export const actionLabels = { check: '체크', call: '콜', fold: '폴드', raise: '레이즈' };
export const raiseLabels = { maximum: '최대', large: '큰', medium: '중간', small: '작은' };
export function eventKey(event) {
  return Number.isSafeInteger(event.event_id) ? `event:${event.round_id}:${event.event_id}`
    : Number.isSafeInteger(event.sequence) ? `stream:${event.sequence}` : null;
}
export function mergePublicEvents(previous, incoming, limit = 2000) {
  const map = new Map(previous.map(event => [eventKey(event), event]));
  for (const event of incoming) {
    const key = eventKey(event);
    if (key) map.set(key, { ...map.get(key), ...event });
  }
  return [...map.values()].sort((a, b) => (a.round_id || 0) - (b.round_id || 0)
    || (a.event_id ?? Infinity) - (b.event_id ?? Infinity)
    || (a.sequence || 0) - (b.sequence || 0)).slice(-limit);
}
export function actionLabel(event) {
  if (event.action !== 'raise') return actionLabels[event.action] || '베팅';
  return `${raiseLabels[event.raise_size] || ''} 레이즈${event.amount ? ` +${event.amount}` : ''}`.trim();
}
export function toEventEntries(events, humanId) {
  return events.map(event => {
    const text = event.type === 'Speech' ? event.text
      : event.type === 'BetPlaced' ? actionLabel(event)
      : event.type === 'RoundStarted' ? '카드를 나눴습니다. 대화를 시작합니다.'
      : event.type === 'PlayerSilent' ? '발언 없음'
      : event.type === 'RoundEnded' ? event.draw ? '무승부입니다. 판돈을 다음 라운드로 넘깁니다.'
        : `${event.winner === humanId ? '나' : event.winner} 승리 · 판돈 ${event.pot}`
      : ({ AgentFailed: '에이전트 응답 오류', RoundReady: '라운드 정리가 끝났습니다.',
        RunFailed: '라운드 실행 오류', MemoryPublishFailed: '경험 저장에 실패했습니다.', GameClosed: '테이블을 종료했습니다.' })[event.type];
    return { id: eventKey(event), roundId: event.round_id, eventId: event.event_id ?? null,
      sequence: event.sequence ?? null, type: event.type, playerId: event.player_id ?? null,
      playerLabel: event.player_id === humanId ? '나' : event.player_id || '테이블', text: text || event.type, frameId: event.frame_id || null };
  });
}
