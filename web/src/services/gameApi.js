/** 일반 HTTP 전송. 게임 권한/칩 계산은 서버에 맡긴다. */
export class ApiError extends Error {
  constructor(message, status = 0, uncertain = false) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.uncertain = uncertain;
  }
}

export function gamePath(context, suffix = '') {
  return `/games/${encodeURIComponent(context.gameId)}${suffix}`;
}

export function createGameApi({ fetchImpl = globalThis.fetch, baseUrl = '', timeoutMs = 125000 } = {}) {
  async function request(path, { method = 'GET', context, body } = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetchImpl(`${baseUrl}${path}`, {
        method, signal: controller.signal, cache: 'no-store',
        headers: { Accept: 'application/json',
          ...(context ? { Authorization: `Bearer ${context.accessToken}` } : {}),
          ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        const detail = typeof data?.detail === 'string' ? data.detail : '요청을 처리하지 못했습니다.';
        throw new ApiError(`${detail} (HTTP ${response.status})`, response.status, method !== 'GET' && response.status >= 500);
      }
      if (!data || typeof data !== 'object') throw new ApiError('서버 응답을 읽을 수 없습니다.', 0, method !== 'GET');
      return data;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError(controller.signal.aborted ? '요청 시간이 초과됐습니다.' : '서버에 연결하지 못했습니다.', 0, method !== 'GET');
    } finally {
      clearTimeout(timeout);
    }
  }
  return {
    checkHealth: () => request('/health'),
    createGame: body => request('/games', { method: 'POST', body }),
    getGame: context => request(gamePath(context), { context }),
    listReplayRounds: context => request(gamePath(context, '/history'), { context }),
    getReplayFrames: (context, roundId) => request(`${gamePath(context, '/history')}?round_id=${encodeURIComponent(roundId)}`, { context }),
    startRound: (context, body) => request(gamePath(context, '/rounds'), { method: 'POST', context, body }),
    submitCommand: (context, body) => request(gamePath(context, '/commands'), { method: 'POST', context, body }),
    releaseSpeech: (context, body) => request(gamePath(context, '/commands/release'), { method: 'POST', context, body }),
    closeGame: context => request(gamePath(context), { method: 'DELETE', context }),
  };
}
const api = createGameApi();
export const checkHealth = (...args) => api.checkHealth(...args);
export const createGame = (...args) => api.createGame(...args);
export const getGame = (...args) => api.getGame(...args);
export const listReplayRounds = (...args) => api.listReplayRounds(...args);
export const getReplayFrames = (...args) => api.getReplayFrames(...args);
export const startRound = (...args) => api.startRound(...args);
export const submitCommand = (...args) => api.submitCommand(...args);
export const releaseSpeech = (...args) => api.releaseSpeech(...args);
export const closeGame = (...args) => api.closeGame(...args);
