import assert from 'node:assert/strict';
import test from 'node:test';
import { createGameApi, ApiError } from '../src/services/gameApi.js';
import { createSseParser } from '../src/services/sseParser.js';
import { subscribeGameEvents } from '../src/services/eventStream.js';

const context = { gameId: 'g', accessToken: 'test-secret' };
const frame = n => `id: ${n}\nevent: Speech\ndata: ${JSON.stringify({ type: 'Speech', sequence: n, text: '안녕' })}\n\n`;
function response(text) {
  const bytes = new TextEncoder().encode(text);
  return new Response(new ReadableStream({ start(controller) {
    // 한글 UTF-8과 CRLF가 임의의 바이트 청크에서 잘리도록 한다.
    for (let i = 0; i < bytes.length; i += 3) controller.enqueue(bytes.slice(i, i + 3));
    controller.close();
  } }), { headers: { 'Content-Type': 'text/event-stream' } });
}

test('HTTP 인증은 헤더에만 넣고 동일 본문을 전송한다', async () => {
  const calls = [];
  const api = createGameApi({ baseUrl: 'http://local', fetchImpl: async (url, options) => {
    calls.push([url, options]); return Response.json({ result: { status: 'accepted' } });
  } });
  const body = { request_id: 'same-id', kind: 'bet' };
  await api.submitCommand(context, body);
  assert.equal(calls[0][0], 'http://local/games/g/commands');
  assert.equal(calls[0][1].headers.Authorization, 'Bearer test-secret');
  assert.equal(calls[0][1].body, JSON.stringify(body));
  assert.equal(calls[0][0].includes('test-secret'), false);
  const offline = createGameApi({ fetchImpl: async () => { throw new TypeError('offline'); } });
  await assert.rejects(offline.submitCommand(context, body), error => error instanceof ApiError && error.uncertain);
  await assert.rejects(offline.getGame(context), error => error instanceof ApiError && !error.uncertain);
});

test('SSE 파서는 분할 CRLF/주석/여러 data 줄을 처리한다', () => {
  const frames = [];
  const parser = createSseParser(value => frames.push(value));
  for (const char of ': heartbeat\r\nid: 4\r\nevent: Speech\r\ndata: {"a":\r\ndata: "한글"}\r\n\r\n') parser.feed(char);
  assert.equal(frames.length, 1);
  assert.equal(frames[0].id, '4');
  assert.deepEqual(JSON.parse(frames[0].data), { a: '한글' });
});

test('SSE 중복 제거와 재연결 커서, 인증 헤더', { timeout: 2000 }, async () => {
  const received = [], calls = [];
  let stream;
  stream = subscribeGameEvents(context, { retryMs: 0, onEvent: event => {
    received.push(event); if (event.sequence === 2) stream.close();
  }, fetchImpl: async (url, options) => {
    calls.push([url, options.headers]);
    return response(calls.length === 1 ? frame(1) + frame(1) : frame(2));
  } });
  await stream.done;
  assert.deepEqual(received.map(event => event.sequence), [1, 2]);
  assert.equal(calls[1][1]['Last-Event-ID'], '1');
  assert.equal(calls[1][1].Authorization, 'Bearer test-secret');
  assert.match(calls[1][0], /after=1$/);
});

test('SSE 409와 ResyncRequired는 현재 상태 커서로 다시 연결한다', { timeout: 2000 }, async () => {
  for (const expired of [true, false]) {
    let calls = 0, resyncs = 0, stream;
    stream = subscribeGameEvents(context, { retryMs: 0,
      onResync: async () => { resyncs += 1; return 9; },
      onEvent: () => stream.close(),
      fetchImpl: async url => {
        calls += 1;
        if (calls === 1) return expired ? Response.json({ detail: 'expired' }, { status: 409 })
          : response('event: ResyncRequired\ndata: {"type":"ResyncRequired"}\n\n');
        assert.match(url, /after=9$/);
        return response(frame(10));
      },
    });
    await stream.done;
    assert.equal(resyncs, 1);
  }
});

test('SSE 인증 실패는 무한 재시도하지 않는다', async () => {
  let calls = 0, status;
  const stream = subscribeGameEvents(context, { onStatus: value => { status = value; },
    fetchImpl: async () => { calls += 1; return Response.json({}, { status: 401 }); } });
  await stream.done;
  assert.equal(calls, 1); assert.equal(status, 'error');
});
