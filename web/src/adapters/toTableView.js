/** 백엔드 snake_case 응답을 읽기 전용 TableView로 변환할 위치.
 * 실시간/과거 화면은 같은 TableView 계약을 사용한다.
 * 하단 human-anchor는 관전 모드에서도 유지하되 엔진 참가자를 만들지 않는다.
 * 좌석/색은 게임 생성 순서 기준으로 유지한다. 베팅 시작 순서로 재배치하지 않는다.
 * 카드 앞면은 해당 시점에 허용된 visible_cards만 사용한다.
 */
export function toTableView(gameState, publicEvents = []) {
  throw new Error('TODO: 관찰 → 화면 전용 데이터 변환 구현');
}
