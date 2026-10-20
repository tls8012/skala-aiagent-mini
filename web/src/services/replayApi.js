/** 신규 백엔드 계약이 필요한 기록 조회 스켈레톤. 현재 호출 가능한 API가 아니다.
 * 제안: GET /games/{id}/history?round_id=...
 * 반환 예정: 완료 라운드 목록 또는 사건별 ReplayFrame 목록.
 * SSE 후 현재 상태를 조회한 결과를 과거 프레임으로 위장하지 않는다.
 */
export async function listReplayRounds(context) {
  throw new Error('TODO: 완료 라운드 기록 API 명세 확정 후 연결');
}

export async function getReplayFrames(context, roundId) {
  throw new Error('TODO: 사건 시점의 관찰 저장/조회 구현 후 연결');
}
