/** 일반 HTTP 클라이언트 스켈레톤. 상대 경로로 웹 프록시를 호출할 예정. */

/** GET /health. proxy와 backend를 구분해 읽는다. */
export async function checkHealth() {
  throw new Error('TODO: checkHealth HTTP 연결 구현');
}

/** POST /games. 생성 응답의 토큰은 호출 계층에서만 보관한다. */
export async function createGame(body) {
  throw new Error('TODO: createGame HTTP 연결 구현');
}

/** GET /games/{id}. 해당 인간/관전자의 현재 관찰만 읽는다. */
export async function getGame(context) {
  throw new Error('TODO: getGame HTTP 연결 구현');
}

/** POST /games/{id}/rounds. body={round_id}. */
export async function startRound(context, body) {
  throw new Error('TODO: startRound HTTP 연결 구현');
}

/** POST /games/{id}/commands. body=기존 PlayerCommand. */
export async function submitCommand(context, body) {
  throw new Error('TODO: submitCommand HTTP 연결 구현');
}

/** POST /games/{id}/commands/release. body={round_id,basis_event}. */
export async function releaseSpeech(context, body) {
  throw new Error('TODO: releaseSpeech HTTP 연결 구현');
}

/** DELETE /games/{id}. SSE 연결 종료와 구분한다. */
export async function closeGame(context) {
  throw new Error('TODO: closeGame HTTP 연결 구현');
}
