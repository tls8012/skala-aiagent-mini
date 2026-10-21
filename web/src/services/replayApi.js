/** 기존 인증된 HTTP 전송을 재사용한다. 과거 조회는 게임 변경 요청을 보내지 않는다. */
import * as defaultApi from './gameApi.js';
export function createReplayApi(api = defaultApi) {
  return {
    listReplayRounds: context => api.listReplayRounds(context),
    getReplayFrames: (context, roundId) => api.getReplayFrames(context, roundId),
  };
}
const api = createReplayApi();
export const listReplayRounds = (...args) => api.listReplayRounds(...args);
export const getReplayFrames = (...args) => api.getReplayFrames(...args);
