/** tests/run_web_integration.py가 띄운 실제 HTTP 프록시/서버로 세션을 검증한다. */
import assert from 'node:assert/strict';
import { createGameApi } from '../src/services/gameApi.js';
import { subscribeGameEvents } from '../src/services/eventStream.js';
import { useGameSession } from '../src/composables/useGameSession.js';

const baseUrl = process.argv[2];
if (!baseUrl) throw new Error('테스트 프록시 URL이 필요합니다.');
const sessions = [];
function storage() { let value = null; return { read: () => value, write: next => { value = next; } }; }
function create(store = storage()) {
  const session = useGameSession({ api: createGameApi({ baseUrl }), storage: store,
    subscribe: (context, callbacks) => subscribeGameEvents(context, { ...callbacks, baseUrl, retryMs: 20 }) });
  sessions.push(session); return session;
}
async function until(condition, label) {
  const start = Date.now();
  while (!condition()) {
    if (Date.now() - start > 10000) throw new Error(`timeout: ${label}`);
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}
async function speakAndCheck(session, text) {
  await until(() => session.commands.value.canSpeak && !session.commands.value.disabled, 'human speech');
  const spoken = await session.speak({ text });
  assert.equal(spoken.result.status, 'accepted');
  await until(() => session.commands.value.allowedBets.includes('check') && !session.commands.value.disabled, 'human betting turn');
  // 다른 AI의 사건이 클릭과 접수 사이에 확정될 수 있다. 거절 후 새 상태에서 다시 선택한다.
  let checked;
  for (let attempt = 0; attempt < 8; attempt += 1) {
    await until(() => session.commands.value.allowedBets.includes('check') && !session.commands.value.disabled, 'fresh betting turn');
    checked = await session.bet({ action: 'check' });
    if (checked.result.status === 'accepted') break;
    assert.equal(checked.result.rejudge, true, JSON.stringify(checked.result));
  }
  assert.equal(checked.result.status, 'accepted', JSON.stringify(checked.result));
  await until(() => session.canStartNextRound.value, 'RoundReady');
}
try {
  const saved = storage(), first = create(saved);
  await first.initialize(); assert.equal(first.health.status, 'ok');
  await first.createGame({ aiIds: ['A', 'B'], humanPlays: true });
  await until(() => first.canStartNextRound.value, 'initial stream');
  const seats = first.view.value.aiSeats.map(seat => [seat.playerId, seat.position, seat.color]);
  await first.startRound();
  assert.equal(first.view.value.humanSeat.card.visibility, 'back');
  await speakAndCheck(first, '나도 참가할게');
  assert.equal(first.liveState.value.phase, 'revealed');
  assert.equal(new Set(first.entries.value.map(entry => entry.id)).size, first.entries.value.length);
  assert.ok(first.entries.value.some(entry => entry.text === '나도 참가할게'));
  await first.startRound();
  assert.equal(first.liveState.value.round_id, 2);
  assert.deepEqual(first.view.value.aiSeats.map(seat => [seat.playerId, seat.position, seat.color]), seats);
  first.dispose(); // 브라우저를 나갔다가 돌아오는 것처럼 구독만 끊는다.
  const restored = create(saved);
  await restored.initialize();
  await until(() => restored.connection.status === 'connected', 'restore stream');
  assert.equal(restored.liveState.value.round_id, 2);
  assert.equal(restored.view.value.humanSeat.card.visibility, 'back');
  assert.ok(restored.entries.value.some(entry => entry.type === 'Speech' && entry.roundId === 2));
  await speakAndCheck(restored, '돌아왔어. 다음 판단을 보자');
  await restored.closeGame();
  assert.equal(restored.liveState.value.status, 'closed'); assert.equal(saved.read(), null);

  const spectator = create(); await spectator.initialize();
  await spectator.createGame({ aiIds: ['A', 'B', 'C'], humanPlays: false });
  await until(() => spectator.canStartNextRound.value, 'spectator stream');
  assert.equal(spectator.view.value.humanSeat.chips, null);
  assert.equal(spectator.view.value.humanSeat.card.visibility, 'absent');
  await spectator.startRound();
  await until(() => spectator.canStartNextRound.value, 'AI-only round ready');
  assert.equal(spectator.liveState.value.phase, 'revealed');
  assert.equal(spectator.commands.value.canSpeak, false);
  assert.ok(spectator.view.value.aiSeats.every(seat => seat.card.visibility === 'face'));
  await spectator.closeGame();
  console.log('Live integration passed: real proxy + SSE, human/AI-only rounds, next round, stable seats, reload recovery, close.');
} finally { for (const session of sessions) session.dispose(); }
