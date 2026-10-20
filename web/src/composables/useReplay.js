/** 리플레이 커서/완료 라운드/읽기 전용 표시 데이터를 관리할 위치.
 * 예정 반환: mode, rounds, frames, selectedFrameId, displayView, loading, error,
 * loadRound, seekFrame, previous, next, first, last, returnLive.
 * liveState를 수정하거나 LLM/게임 실행을 다시 호출하지 않는다.
 * 정확한 사건별 과거 상태 API는 아직 백엔드에 없다. UI_SPEC의 선행 작업 참고.
 */
export function useReplay() {
  throw new Error('TODO: 리플레이 상태와 커서 구현');
}
