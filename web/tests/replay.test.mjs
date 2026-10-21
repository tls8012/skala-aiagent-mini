import assert from 'node:assert/strict';
import test from 'node:test';
import { computed, ref } from 'vue';
import { useReplay } from '../src/composables/useReplay.js';
import { toTableView } from '../src/adapters/toTableView.js';

const clone = value => structuredClone(value);
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
function frame(round, eventId, type = 'Speech', game = 'g') {
  const event = { event_id: eventId, round_id: round, type, player_id: 'A', text: `발언 ${eventId}`, channel: 'game' };
  return { frame_id: `frame-${game}-${eventId}`, game_id: game, round_id: round, event_id: eventId, event,
    snapshot: { game_id: game, round_id: round, event_id: eventId, phase: type === 'RoundEnded' ? 'revealed' : 'dialogue',
      human_id: 'human', perspective: 'player', pot: eventId, current_bet: 0, betting_turn: 'A', folded: [],
      players: ['A', 'B', 'human'].map(id => ({ id, kind: id === 'human' ? 'human' : 'ai', chips: 28, failed: false, eliminated: false })),
      visible_cards: type === 'RoundEnded' ? { A: { rank: 14, suit: 'S' }, human: { rank: 13, suit: 'H' } } : { A: { rank: 14, suit: 'S' } },
      human_display: { participation: 'playing', remaining_utterances: 4 }, display_events: type === 'Speech' ? [event] : [] } };
}
function fixture() {
  const records = new Map([[1, [frame(1, 7), frame(1, 12, 'RoundEnded')]], [2, [frame(2, 20), frame(2, 27)]]]);
  const liveState = ref({ game_id: 'g', round_id: 2, event_id: 27, stream_cursor: 30, status: 'running', phase: 'dialogue', pot: 27 });
  const calls = [];
  const session = { liveState, view: computed(() => toTableView(liveState.value)),
    entries: ref([...records.values()].flat().map(f => ({ frameId: f.frame_id, roundId: f.round_id }))),
    listReplayRounds: async () => ({ game_id: liveState.value.game_id, rounds: [...records].map(([round, frames]) => ({
      round_id: round, frame_count: frames.length, last_frame_id: frames.at(-1).frame_id, completed: frames.at(-1).event.type === 'RoundEnded',
    })) }),
    getReplayFrames: async round => { calls.push(round); return clone({ game_id: liveState.value.game_id, round_id: round, frames: records.get(round) }); },
  };
  const replay = useReplay(session);
  return { session, replay, records, calls };
}

test('사건 ID로 정확한 프레임을 선택하고 처음/이전/다음/마지막/슬라이더로 이동한다', async () => {
  const f = fixture();
  await f.replay.seekFrame('frame-g-12');
  assert.equal(f.replay.selectedFrame.value.event_id, 12);
  assert.equal(f.replay.position.value, 1); assert.equal(f.replay.displayView.value.summary.pot, 12);
  f.replay.step('first'); assert.equal(f.replay.selectedFrame.value.event_id, 7);
  assert.equal(f.replay.displayView.value.aiSeats[0].speech.text, '발언 7');
  assert.deepEqual(f.replay.displayView.value.humanSeat.card, { visibility: 'back' });
  f.replay.step('next'); assert.equal(f.replay.selectedFrame.value.event_id, 12);
  f.replay.step('previous'); assert.equal(f.replay.selectedFrame.value.event_id, 7);
  f.replay.step('last'); assert.equal(f.replay.selectedFrame.value.event_id, 12);
  f.replay.seekPosition(0); assert.equal(f.replay.selectedFrame.value.event_id, 7);
  assert.deepEqual(f.replay.entries.value.map(e => e.frameId), ['frame-g-7', 'frame-g-12']);
  assert.deepEqual(f.calls, [1]);
  f.replay.returnLive(); assert.equal(f.replay.mode.value, 'live'); assert.equal(f.replay.displayView.value, null);
  f.replay.dispose();
});

test('과거를 보는 중 새 사건/다음 라운드는 실시간에만 반영되고 현재 복귀는 명시적이다', async () => {
  const f = fixture(); await f.replay.seekFrame('frame-g-20');
  const selected = f.replay.selectedFrame.value;
  f.records.get(2).push(frame(2, 31, 'RoundEnded'));
  f.session.liveState.value = { ...f.session.liveState.value, round_id: 3, event_id: 32, stream_cursor: 35, pot: 99 };
  await tick(); await f.replay.refreshRounds();
  assert.equal(f.replay.selectedFrame.value, selected);
  assert.equal(f.replay.displayView.value.summary.roundId, 2);
  assert.equal(f.replay.displayView.value.summary.pot, 20);
  assert.equal(f.replay.state.value.unreadCount, 5);
  assert.equal(f.replay.frames.value.length, 3);
  f.replay.step('last');
  assert.equal(f.replay.mode.value, 'replay'); assert.equal(f.replay.selectedFrame.value.event_id, 31);
  f.replay.returnLive(); assert.equal(f.session.view.value.summary.pot, 99);
  f.replay.dispose();
});

test('느린 A 조회 뒤 빠른 B 선택은 늦은 A 응답으로 되돌아가지 않는다', async () => {
  const f = fixture(), slow = deferred(), original = f.session.getReplayFrames;
  f.session.getReplayFrames = round => round === 1 ? slow.promise : original(round);
  const pending = f.replay.seekFrame('frame-g-7');
  assert.equal(f.replay.mode.value, 'replay'); assert.equal(f.replay.loading.value, true);
  await f.replay.seekFrame('frame-g-27');
  slow.resolve({ game_id: 'g', round_id: 1, frames: clone(f.records.get(1)) }); await pending;
  assert.equal(f.replay.selectedFrame.value.event_id, 27);
  assert.equal(f.replay.loading.value, false); f.replay.dispose();
});

test('조회 실패는 이전 화면을 유지하며 재시도/현재 복귀가 가능하다', async () => {
  const f = fixture(); await f.replay.seekFrame('frame-g-7');
  const previous = f.replay.displayView.value, original = f.session.getReplayFrames;
  f.session.getReplayFrames = async () => { throw new Error('temporary failure'); };
  await f.replay.seekFrame('frame-g-27');
  assert.equal(f.replay.error.value, 'temporary failure');
  assert.deepEqual(f.replay.displayView.value, previous);
  assert.equal(f.replay.mode.value, 'replay');
  f.session.getReplayFrames = original; await f.replay.retry();
  assert.equal(f.replay.selectedFrame.value.event_id, 27); assert.equal(f.replay.error.value, null);
  await f.replay.seekFrame('missing'); assert.match(f.replay.error.value, /링크/);
  f.replay.returnLive(); assert.equal(f.replay.error.value, null); f.replay.dispose();
});

test('캐시와 늦은 응답은 게임 변경/종료 이후 남지 않는다', async () => {
  const f = fixture(), slow = deferred();
  f.session.getReplayFrames = () => slow.promise;
  const pending = f.replay.seekFrame('frame-g-7');
  f.session.liveState.value = { ...f.session.liveState.value, game_id: 'other', stream_cursor: 0 };
  slow.resolve({ game_id: 'g', round_id: 1, frames: clone(f.records.get(1)) }); await pending;
  assert.equal(f.replay.selectedFrame.value, null); assert.equal(f.replay.frames.value.length, 0);
  assert.equal(f.replay.mode.value, 'live');
  f.session.liveState.value = { ...f.session.liveState.value, status: 'closed' };
  assert.equal(f.replay.rounds.value.length, 0); assert.equal(f.replay.state.value.available, false);
  f.replay.dispose();
});

test('프레임 캐시는 재조회 때 과거 카드/대화를 덮어쓰지 않고 새로운 프레임만 더한다', async () => {
  const f = fixture(); await f.replay.seekFrame('frame-g-20');
  const before = clone(f.replay.selectedFrame.value);
  f.records.get(2)[0].snapshot.visible_cards.human = { rank: 13, suit: 'H' };
  f.records.get(2)[0].event.text = '뒤늦은 변조';
  f.records.get(2).push(frame(2, 30, 'RoundEnded'));
  await f.replay.refreshRounds();
  assert.deepEqual(f.replay.selectedFrame.value, before);
  assert.equal(f.replay.frames.value.length, 3);
  assert.equal(f.replay.displayView.value.humanSeat.card.visibility, 'back'); f.replay.dispose();
});

test('과거 화면에서는 모든 게임 변경 경로를 차단하고 조회/복귀만 허용한다', async () => {
  const f = fixture(); let calls = 0;
  const change = () => { calls += 1; };
  f.replay.runLive(change); assert.equal(calls, 1);
  await f.replay.seekFrame('frame-g-7');
  for (const _ of ['create', 'start', 'close', 'speak', 'silent', 'release', 'bet', 'retryCommand']) f.replay.runLive(change);
  assert.equal(calls, 1);
  await f.replay.selectRound(2); f.replay.returnLive(); f.replay.runLive(change);
  assert.equal(calls, 2); f.replay.dispose();
  f.replay.runLive(change); assert.equal(calls, 2);
});

test('조회 중 현재 복귀/정리 이후 응답은 화면을 바꾸지 않는다', async () => {
  const f = fixture(), slow = deferred();
  f.session.getReplayFrames = () => slow.promise;
  const pending = f.replay.selectRound(1);
  const held = f.replay.displayView.value;
  f.session.liveState.value = { ...f.session.liveState.value, pot: 99 };
  assert.equal(f.replay.displayView.value, held);
  f.replay.returnLive();
  slow.resolve({ game_id: 'g', round_id: 1, frames: clone(f.records.get(1)) }); await pending;
  assert.equal(f.replay.mode.value, 'live'); assert.equal(f.replay.selectedFrame.value, null);
  f.replay.dispose();
});

test('기록 조회 오류는 조회 복구 시 지워지며 잘못된 프레임은 표시하지 않는다', async () => {
  const f = fixture(); await tick(); const list = f.session.listReplayRounds;
  f.session.listReplayRounds = async () => { throw new Error('목록 오류'); };
  await f.replay.refreshRounds(); assert.equal(f.replay.error.value, '목록 오류');
  f.session.listReplayRounds = list; await f.replay.refreshRounds(); assert.equal(f.replay.error.value, null);
  f.records.get(1)[0].snapshot.event_id = 999;
  await f.replay.seekFrame('frame-g-7');
  assert.match(f.replay.error.value, /올바른 과거/); assert.equal(f.replay.selectedFrame.value, null);
  f.replay.dispose();
});
