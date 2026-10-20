/** 실시간 원본 상태와 명령 접수/연결 수명을 관리할 위치.
 * 예정 반환: liveState, connection, commandState,
 * createGame, refresh, startRound, speak, silent, bet, releaseSpeech, closeGame, dispose.
 * access_token은 하위 컴포넌트/화면 프레임/로그에 전달하지 않는다.
 * 과거 프레임의 round_id/event_id로 명령을 만들지 않는다.
 */
export function useGameSession() {
  throw new Error('TODO: 실시간 게임 상태와 명령 조립 구현');
}
