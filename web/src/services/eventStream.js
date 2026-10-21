import { gamePath } from './gameApi.js';
import { createSseParser } from './sseParser.js';

/** close()는 구독만 닫는다. 게임 DELETE는 호출하지 않는다. */
export function subscribeGameEvents(context, {
  after = 0, onEvent = () => {}, onStatus = () => {}, onError = () => {}, onResync = async () => after,
  fetchImpl = globalThis.fetch, baseUrl = '', retryMs = 1000,
} = {}) {
  let cursor = after, closed = false, controller, retryTimer, wakeRetry;
  const sleep = ms => new Promise(resolve => { wakeRetry = resolve; retryTimer = setTimeout(resolve, ms); });
  async function resync() {
    const next = await onResync();
    if (!Number.isSafeInteger(next) || next < 0) throw new Error('상태 재동기화에 실패했습니다.');
    cursor = next;
  }
  const done = (async () => {
    let attempts = 0;
    while (!closed) {
      controller = new AbortController();
      let reader;
      onStatus(attempts ? 'reconnecting' : 'connecting');
      try {
        const response = await fetchImpl(`${baseUrl}${gamePath(context, '/events')}?after=${cursor}`, {
          signal: controller.signal, cache: 'no-store',
          headers: { Authorization: `Bearer ${context.accessToken}`, Accept: 'text/event-stream', 'Last-Event-ID': String(cursor) },
        });
        if (closed) break;
        if ([401, 403, 404].includes(response.status)) {
          await response.body?.cancel();
          onError({ status: response.status, message: '게임 연결 정보를 확인할 수 없습니다.' });
          onStatus('error'); break;
        }
        if (response.status === 409) {
          await response.body?.cancel();
          await resync(); attempts += 1; continue;
        }
        if (!response.ok || !response.body || !response.headers.get('content-type')?.includes('text/event-stream')) {
          await response.body?.cancel();
          throw new Error('사건 스트림에 연결하지 못했습니다.');
        }
        onStatus('connected');
        attempts = 0;
        let needsResync = false;
        const parser = createSseParser(frame => {
          const record = JSON.parse(frame.data);
          if (!record || typeof record !== 'object' || record.type !== frame.event) throw new Error('잘못된 사건 응답입니다.');
          if (record.type === 'ResyncRequired') { needsResync = true; return; }
          const sequence = Number(frame.id);
          if (!Number.isSafeInteger(sequence) || sequence < 1 || record.sequence !== sequence) throw new Error('잘못된 사건 번호입니다.');
          if (sequence <= cursor) return;
          onEvent(record);
          cursor = sequence;
        });
        reader = response.body.getReader();
        const decoder = new TextDecoder();
        while (!closed && !needsResync) {
          const chunk = await reader.read();
          if (chunk.done) break;
          parser.feed(decoder.decode(chunk.value, { stream: true }));
        }
        if (needsResync && !closed) await resync();
        if (!closed) onStatus('reconnecting');
      } catch (error) {
        if (!closed) { onStatus('reconnecting'); onError({ status: 0, message: error.message }); }
      } finally {
        await reader?.cancel().catch(() => {});
        controller.abort();
      }
      if (!closed) { attempts += 1; await sleep(Math.min(retryMs * 2 ** Math.min(attempts - 1, 4), 15000)); }
    }
  })();
  return { done, close() { closed = true; controller?.abort(); clearTimeout(retryTimer); wakeRetry?.(); } };
}
