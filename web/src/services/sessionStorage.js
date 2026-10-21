/** 한 탭의 재접속 정보만 보관한다. 사건/화면 props에는 토큰을 복사하지 않는다. */
const key = 'indian-poker-session-v1';
export function createSessionStorage(storage) {
  if (storage === undefined) { try { storage = globalThis.sessionStorage; } catch { /* 저장 접근 차단 */ } }
  return {
    read() {
      try {
        const value = JSON.parse(storage?.getItem(key) || 'null');
        return typeof value?.gameId === 'string' && typeof value.accessToken === 'string'
          ? { gameId: value.gameId, accessToken: value.accessToken } : null;
      } catch { return null; }
    },
    write(context) {
      try { if (context) storage?.setItem(key, JSON.stringify(context)); else storage?.removeItem(key); } catch { /* 저장 차단 시 현재 탭에서만 플레이 */ }
    },
  };
}
