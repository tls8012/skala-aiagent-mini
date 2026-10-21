import assert from 'node:assert/strict';
import test from 'node:test';
import { watch } from 'vue';
import { useGameSession } from '../src/composables/useGameSession.js';
import { ApiError } from '../src/services/gameApi.js';
import { toTableView } from '../src/adapters/toTableView.js';
import { toCommandState } from '../src/adapters/toCommandState.js';

const clone = value => structuredClone(value);
const tick = () => new Promise(resolve => setImmediate(resolve));

test('인간이 직전 발언자여도 서버가 허용하면 입력과 기존 보류 공개가 가능하다', () => {
  const game = { game_id: 'g', round_id: 1, phase: 'dialogue', status: 'running', human_id: 'human',
    players: [{ id: 'human', kind: 'human', eliminated: false, failed: false }],
    observation: { can_speak: true, last_speaker: 'human', remaining_utterances: 4, allowed_bets: [] },
    deferred_speech: { text: '기존 보류 원문' } };
  const commands = toCommandState(game, { synchronized: true });
  assert.equal(commands.canSpeak, true); assert.equal(commands.canRelease, true);
  game.observation.can_speak = false; game.observation.remaining_utterances = 0;
  const exhausted = toCommandState(game, { synchronized: true });
  assert.equal(exhausted.canSpeak, false); assert.equal(exhausted.canRelease, false);
});
function fixture() {
  let state = { game_id: 'g', status: 'created', round_id: 0, event_id: 0, stream_cursor: 0,
    human_id: 'human', players: ['A', 'B', 'human'].map(id => ({ id, kind: id === 'human' ? 'human' : 'ai', chips: 30, eliminated: false, failed: false })),
    phase: 'lobby', folded: [], visible_cards: {}, recent_events: [], observation: null,
    can_start_next_round: true, rules: { min_utterances: 1, max_message_chars: 4000 } };
  let callbacks, saved = null;
  const api = {
    checkHealth: async () => ({ status: 'ok', llm_backend: 'fake' }),
    createGame: async () => ({ access_token: 'private-token', game: clone(state) }),
    getGame: async () => clone(state),
    startRound: async () => {
      state = { ...state, status: 'running', round_id: 1, phase: 'dialogue', event_id: 1, stream_cursor: 1,
        can_start_next_round: false, visible_cards: { A: { rank: 14, suit: 'S' } },
        observation: { can_speak: true, remaining_utterances: 5, required_game_utterances: 1, last_speaker: 'B', allowed_bets: [], raise_options: {} } };
      return clone(state);
    },
    submitCommand: async body => body,
    closeGame: async () => ({ status: 'closed' }),
  };
  const session = useGameSession({ api, subscribe: (context, handlers) => {
    callbacks = handlers; handlers.onStatus('connected'); return { close() {} };
  }, storage: { read: () => saved, write: value => { saved = value; } } });
  return { api, session, get state() { return state; }, set state(value) { state = value; },
    get callbacks() { return callbacks; }, get saved() { return saved; } };
}
async function open(f) { await f.session.createGame({ aiIds: ['A', 'B'], humanPlays: true }); await f.session.startRound(); }
function accepted(f, body) {
  const event = { type: 'Speech', round_id: 1, event_id: 2, sequence: 2, player_id: 'human', channel: 'game', text: body.text, request_id: body.request_id };
  f.state = { ...f.state, event_id: 2, stream_cursor: 2, recent_events: [event],
    observation: { ...f.state.observation, remaining_utterances: 4, required_game_utterances: 0 } };
  return { result: { status: 'accepted' }, game: clone(f.state) };
}

test('초기화는 자동으로 게임을 만들지 않는다', async () => {
  const f = fixture(); await f.session.initialize();
  assert.equal(f.session.liveState.value, null); assert.equal(f.saved, null);
  assert.equal(Boolean(f.session.sessionSummary.value.gameWinner), false);
  assert.equal(f.session.view.value.summary.bettingTurn, null);
  assert.equal(f.session.health.backend, 'fake'); f.session.dispose();
});

test('연결 안내는 현재 공급자의 모델을 표시한다', async () => {
  const f = fixture();
  f.api.checkHealth = async () => ({ status: 'ok', llm_backend: 'openai', llm_model: 'gpt-6-luna', ollama_model: 'qwen3:8b' });
  await f.session.initialize();
  assert.equal(f.session.health.backend, 'openai');
  assert.equal(f.session.health.model, 'gpt-6-luna');
  f.session.dispose();
});

test('접수 불명확 시 같은 요청 ID/본문으로만 재확인한다', async () => {
  const f = fixture(); await open(f);
  const bodies = [];
  f.api.submitCommand = async (context, body) => {
    bodies.push(clone(body));
    if (bodies.length === 1) throw new ApiError('offline', 0, true);
    return accepted(f, body);
  };
  await f.session.speak({ text: '내 발언' });
  assert.equal(f.session.canRetry.value, true); assert.equal(f.session.commands.value.disabled, true);
  await f.session.speak({ text: '다른 발언' }); assert.equal(bodies.length, 1);
  await f.session.retryLastCommand();
  assert.deepEqual(bodies[0], bodies[1]);
  assert.equal(f.session.commands.value.speechReceipt.status, 'accepted');
  assert.equal(f.session.canRetry.value, false);
  f.callbacks.onEvent(f.state.recent_events[0]); await tick();
  assert.equal(f.session.entries.value.filter(event => event.type === 'Speech').length, 1);
  assert.equal(JSON.stringify([f.session.view.value, f.session.entries.value, f.session.commands.value, f.session.sessionSummary.value]).includes('private-token'), false);
  f.session.dispose();
});

test('발언 응답을 잃어도 공개 Speech로 접수를 복구한다', async () => {
  const f = fixture(); await open(f);
  f.api.submitCommand = async (context, body) => { accepted(f, body); throw new ApiError('lost response', 0, true); };
  await f.session.speak({ text: '이미 접수된 발언' });
  assert.equal(f.session.canRetry.value, false);
  assert.equal(f.session.commands.value.speechReceipt.status, 'accepted'); f.session.dispose();
});

test('명령 전 시작된 느린 GET이 최신 접수 결과를 덮어쓰지 않는다', async () => {
  const f = fixture(); await open(f);
  const old = clone(f.state);
  let release, started;
  const entered = new Promise(resolve => { started = resolve; });
  let calls = 0;
  f.api.getGame = async () => {
    if (++calls === 1) { started(); return new Promise(resolve => { release = () => resolve(old); }); }
    return clone(f.state);
  };
  const refreshing = f.session.refresh(); await entered;
  f.api.submitCommand = async (context, body) => accepted(f, body);
  const observations = [];
  const stop = watch(f.session.liveState, value => observations.push(value.event_id), { flush: 'sync' });
  const speaking = f.session.speak({ text: '새 발언' });
  await tick(); release(); await Promise.all([speaking, refreshing]);
  assert.equal(f.session.liveState.value.event_id, 2);
  assert.equal(observations.includes(1), false);
  stop(); f.session.dispose();
});

test('같은 event_id여도 RoundReady 전송 번호가 높으면 다음 라운드를 연다', async () => {
  const f = fixture(); await open(f);
  f.state = { ...f.state, status: 'ready', phase: 'revealed', stream_cursor: 2, can_start_next_round: true };
  f.callbacks.onEvent({ type: 'RoundReady', sequence: 2, round_id: 1 }); await tick();
  assert.equal(f.session.canStartNextRound.value, true);
  assert.equal(f.session.liveState.value.status, 'ready');
  f.callbacks.onEvent({ type: 'GameClosed', sequence: 3 });
  assert.equal(f.session.liveState.value.status, 'closed'); assert.equal(f.saved, null);
  assert.equal(f.session.canCreate.value, true); f.session.dispose();
});

test('보류 발언 공개의 응답을 잃어도 원래 요청 ID의 Speech로 확인한다', async () => {
  const f = fixture(); await open(f);
  f.state = { ...f.state, deferred_speech: { request_id: 'deferred-original', text: '보류 원문' } };
  await f.session.refresh();
  f.api.releaseSpeech = async () => {
    accepted(f, { text: '보류 원문', request_id: 'deferred-original' });
    f.state.deferred_speech = null;
    throw new ApiError('lost response', 0, true);
  };
  await f.session.releaseSpeech();
  assert.equal(f.session.canRetry.value, false);
  assert.equal(f.session.entries.value.filter(entry => entry.text === '보류 원문').length, 1);
  f.session.dispose();
});

test('구독을 정리한 뒤 도착한 GET 응답은 무시한다', async () => {
  const f = fixture(); await open(f);
  let finish;
  f.api.getGame = () => new Promise(resolve => { finish = resolve; });
  const refreshing = f.session.refresh();
  f.session.dispose();
  finish({ ...f.state, event_id: 99, stream_cursor: 99 }); await refreshing;
  assert.equal(f.session.liveState.value.event_id, 1);
});

test('상태 조회가 복구되면 이전 연결 오류 안내를 지운다', async () => {
  const f = fixture(); await open(f);
  f.api.getGame = async () => { throw new ApiError('temporary failure', 503); };
  await f.session.refresh();
  assert.equal(f.session.error.value, 'temporary failure');
  f.api.getGame = async () => clone(f.state);
  await f.session.refresh();
  assert.equal(f.session.error.value, null); assert.equal(f.session.connection.error, null);
  f.session.dispose();
});

test('늦은 응답/새로고침에서 허용하지 않은 카드는 앞면으로 만들지 않는다', () => {
  const game = { game_id: 'g', human_id: 'human', round_id: 1, event_id: 1, phase: 'dialogue',
    observation: {}, players: [{ id: 'A', kind: 'ai', chips: 28 }, { id: 'B', kind: 'ai', chips: 28 }, { id: 'human', kind: 'human', chips: 28 }],
    visible_cards: { A: { rank: 14, suit: 'S' }, human: { rank: 13, suit: 'H' } }, folded: ['A'] };
  const view = toTableView(game, [{ type: 'RoundEnded', event_id: 9, round_id: 1, revealed_cards: game.visible_cards }]);
  assert.deepEqual(view.humanSeat.card, { visibility: 'back' });
  assert.deepEqual(view.aiSeats[0].card, { visibility: 'back' });
  game.human_id = null; game.observation = null;
  assert.equal(toTableView(game).aiSeats.every(seat => seat.card.visibility === 'back'), true);
  assert.equal(toTableView(game).humanSeat.card.visibility, 'absent');
  game.betting_turn = null;
  assert.equal(toTableView(game).summary.bettingTurn, null);
  const next = toTableView({ ...game, round_id: 2, visible_cards: {} });
  assert.deepEqual(next.aiSeats.map(seat => seat.position), view.aiSeats.map(seat => seat.position));
});
