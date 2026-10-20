/** SSE 연결 전용 스켈레톤.
 * GET /games/{id}/events. fetch 스트림으로 Bearer/Last-Event-ID를 전달할 예정.
 * sequence는 재연결용이며 명령 basis_event와 다르다.
 * 예정 반환: { close } — 구독만 종료한다. 게임 DELETE를 호출하지 않는다.
 * named event, heartbeat, 여러 줄/분할 수신, 재연결, ResyncRequired를 처리할 위치.
 */
export function subscribeGameEvents(context, { after, onEvent, onStatus, onError }) {
  throw new Error('TODO: SSE 연결/해제와 프레임 파싱 구현');
}
