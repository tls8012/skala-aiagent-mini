import { computed, getCurrentScope, onScopeDispose, reactive, ref } from 'vue';
import * as defaultApi from '../services/gameApi.js';
import { subscribeGameEvents } from '../services/eventStream.js';
import { createSessionStorage } from '../services/sessionStorage.js';
import { mergePublicEvents, toEventEntries } from '../adapters/publicEvents.js';
import { toTableView } from '../adapters/toTableView.js';
import { toCommandState } from '../adapters/toCommandState.js';
import { createReplayApi } from '../services/replayApi.js';

/** HTTP 결과와 SSE를 조립한다. 토큰/재전송 본문은 이 클로저 밖으로 내보내지 않는다. */
export function useGameSession({ api = defaultApi, subscribe = subscribeGameEvents, storage = createSessionStorage() } = {}) {
  const liveState = ref(null), events = ref([]), pending = ref(false), error = ref(null), speechReceipt = ref(null);
  const uncertain = ref(false), restoring = ref(false);
  const connection = reactive({ status: 'idle', error: null });
  const health = reactive({ status: 'checking', backend: null, model: null, error: null });
  const replayApi = createReplayApi(api);
  let context = null, stream = null, generation = 0, mutationEpoch = 0, disposed = false;
  let refreshFlight = null, refreshScheduled = false, seenSequence = 0, retryRecord = null, lastRefreshError = null;
  const current = g => !disposed && g === generation && context !== null;
  const synchronized = computed(() => connection.status === 'connected'
    && (liveState.value?.stream_cursor ?? -1) >= seenSequence);
  const commands = computed(() => toCommandState(liveState.value, {
    pending: pending.value, synchronized: synchronized.value, uncertain: uncertain.value,
    error: error.value, speechReceipt: speechReceipt.value,
  }));
  const view = computed(() => toTableView(liveState.value, events.value));
  const entries = computed(() => toEventEntries(events.value, liveState.value?.human_id));
  const participation = computed(() => view.value.humanSeat.participation);
  const remaining = computed(() => liveState.value?.observation?.remaining_utterances || 0);
  const sessionSummary = computed(() => ({ gameId: liveState.value?.game_id, status: liveState.value?.status,
    roundId: liveState.value?.round_id || 0, participation: participation.value,
    gameWinner: liveState.value?.game_winner && liveState.value.game_winner === liveState.value.human_id ? '나' : liveState.value?.game_winner,
    pending: pending.value || uncertain.value || restoring.value, error: liveState.value?.error }));
  const canCreate = computed(() => !pending.value && !restoring.value && !uncertain.value
    && (!liveState.value || liveState.value.status === 'closed') && context === null);
  const canStartNextRound = computed(() => Boolean(liveState.value?.can_start_next_round)
    && synchronized.value && !pending.value && !uncertain.value);

  function ingest(incoming) {
    events.value = mergePublicEvents(events.value, incoming, liveState.value?.rules?.event_history_limit || 2000);
    // HTTP 성공 응답을 잃어도 자기 Speech 사건으로 접수를 확인할 수 있다.
    if (retryRecord?.type === 'release' || retryRecord?.type === 'command' && retryRecord.body.kind === 'speak') {
      const requestId = retryRecord.type === 'release' ? retryRecord.speechRequestId : retryRecord.body.request_id;
      const confirmed = requestId && events.value.find(event => event.type === 'Speech' && event.player_id === liveState.value?.human_id
        && event.round_id === retryRecord.body.round_id && event.request_id === requestId);
      if (confirmed) { acknowledge(retryRecord, { status: 'accepted' }); retryRecord = null; uncertain.value = false; error.value = null; }
    }
  }
  function applySnapshot(snapshot) {
    if (!snapshot || snapshot.game_id !== context?.gameId) return false;
    const old = liveState.value;
    if (old?.status === 'closed') return false;
    if (old && (snapshot.stream_cursor < old.stream_cursor
      || snapshot.stream_cursor === old.stream_cursor && snapshot.event_id < old.event_id)) return false;
    liveState.value = snapshot;
    ingest(snapshot.recent_events || []);
    return true;
  }
  function detach() { stream?.close(); stream = null; }
  function markClosed(message = null) {
    detach(); generation += 1; context = null; storage.write(null);
    if (liveState.value) liveState.value = { ...liveState.value, status: 'closed', betting_turn: null, can_start_next_round: false };
    pending.value = false; uncertain.value = false; retryRecord = null;
    connection.status = 'disconnected'; connection.error = null;
    if (message) error.value = message;
  }
  async function refresh() {
    if (!context || disposed) return null;
    const g = generation;
    if (refreshFlight?.generation === g) { refreshFlight.again = true; return refreshFlight.promise; }
    const flight = { generation: g, again: false, promise: null };
    refreshFlight = flight;
    flight.promise = (async () => {
      let last = null;
      do {
        flight.again = false;
        const epoch = mutationEpoch, requestContext = context;
        try {
          const snapshot = await api.getGame(requestContext);
          if (!current(g)) return null;
          // 명령 접수 전 시작된 조회가 접수 결과를 덮어쓰지 못하게 한다.
          if (epoch !== mutationEpoch) { flight.again = true; continue; }
          applySnapshot(snapshot); last = liveState.value;
          if (lastRefreshError) {
            if (error.value === lastRefreshError) error.value = null;
            if (connection.error === lastRefreshError) connection.error = null;
            lastRefreshError = null;
          }
        } catch (failure) {
          if (current(g)) {
            if ([401, 403, 404].includes(failure.status)) markClosed('이전 게임에 접속할 수 없습니다. 새 테이블을 만들어 주세요.');
            else { lastRefreshError = failure.message; connection.error = failure.message; error.value = failure.message; }
          }
          return null;
        }
      } while (flight.again && current(g));
      return last;
    })().finally(() => { if (refreshFlight === flight) refreshFlight = null; });
    return flight.promise;
  }
  function requestRefresh() {
    if (refreshScheduled) return;
    refreshScheduled = true;
    queueMicrotask(() => { refreshScheduled = false; void refresh(); });
  }
  function attach() {
    if (!context || disposed) return;
    detach();
    const g = generation;
    stream = subscribe({ ...context }, {
      after: liveState.value?.stream_cursor || 0,
      onStatus(status) { if (current(g)) { connection.status = status; if (status === 'connected') connection.error = null; } },
      onError(failure) {
        if (!current(g)) return;
        if ([401, 403, 404].includes(failure.status)) markClosed('게임 연결이 종료됐습니다. 새 테이블을 만들어 주세요.');
        else connection.error = failure.message;
      },
      onEvent(event) {
        if (!current(g)) return;
        seenSequence = Math.max(seenSequence, event.sequence);
        ingest([{ ...event, round_id: event.round_id ?? liveState.value?.round_id ?? 0 }]);
        if (event.type === 'GameClosed') markClosed();
        else requestRefresh();
      },
      async onResync() {
        const snapshot = await refresh();
        if (!snapshot) throw new Error('현재 상태를 다시 불러오지 못했습니다.');
        seenSequence = snapshot.stream_cursor;
        return snapshot.stream_cursor;
      },
    });
  }
  async function checkServer() {
    health.status = 'checking'; health.error = null;
    try {
      const result = await api.checkHealth();
      const details = result.backend?.details || result;
      health.status = result.status === 'ok' ? 'ok' : 'error';
      health.backend = details.llm_backend || null; health.model = details.llm_model || details.ollama_model || null;
    } catch (failure) { health.status = 'error'; health.error = failure.message; }
  }
  async function initialize() {
    if (disposed) return;
    const saved = storage.read();
    if (saved && !context) {
      context = saved; generation += 1; restoring.value = true;
      try { if (await refresh()) attach(); } finally { restoring.value = false; }
    }
    await checkServer();
  }
  async function reconnect() {
    await checkServer();
    if (context && await refresh()) attach();
  }
  async function createGame({ aiIds, humanPlays }) {
    if (!canCreate.value || disposed) return;
    pending.value = true; error.value = null;
    const g = generation;
    try {
      const result = await api.createGame({ ai_ids: aiIds, human_id: humanPlays ? 'human' : null });
      if (disposed || g !== generation) return;
      if (!result.access_token || !result.game?.game_id) throw new Error('테이블 생성 응답을 읽을 수 없습니다.');
      generation += 1; context = { gameId: result.game.game_id, accessToken: result.access_token };
      events.value = []; liveState.value = null; seenSequence = result.game.stream_cursor;
      speechReceipt.value = null; storage.write(context); applySnapshot(result.game); attach();
    } catch (failure) { if (!disposed) error.value = failure.message; }
    finally { pending.value = false; }
  }
  function acknowledge(record, result) {
    if (record.type === 'command' && record.body.kind === 'speak') speechReceipt.value = {
      id: record.body.request_id, contextKey: record.contextKey, status: result.status, text: record.body.text,
    };
  }
  async function perform(record) {
    if (!context || pending.value || disposed) return null;
    const g = generation, requestContext = { ...context };
    pending.value = true; error.value = null; mutationEpoch += 1;
    try {
      const response = record.type === 'start' ? await api.startRound(requestContext, record.body)
        : record.type === 'close' ? await api.closeGame(requestContext)
        : record.type === 'release' ? await api.releaseSpeech(requestContext, record.body)
        : await api.submitCommand(requestContext, record.body);
      if (!current(g)) return response;
      mutationEpoch += 1; uncertain.value = false; retryRecord = null;
      if (record.type === 'close') { markClosed(); return response; }
      applySnapshot(response.game || response);
      if (response.result) {
        acknowledge(record, response.result);
        if (response.result.status === 'rejected') error.value = response.result.rejudge
          ? '상황이 바뀌었어요. 최신 상태를 확인하고 다시 선택해 주세요.' : response.result.reason || '명령이 거절됐습니다.';
      }
      await refresh();
      return response;
    } catch (failure) {
      if (current(g)) {
        mutationEpoch += 1;
        if ([401, 403, 404].includes(failure.status)) markClosed('게임에 접속할 수 없습니다. 새 테이블을 만들어 주세요.');
        else {
          error.value = failure.message;
          if (failure.uncertain) { retryRecord = record; uncertain.value = true; }
          await refresh();
        }
      }
      return null;
    } finally { if (g === generation) pending.value = false; }
  }
  function command(kind, payload = {}) {
    if (!context || pending.value || commands.value.disabled) return;
    if (kind === 'speak' && (!commands.value.canSpeak || !payload.text?.trim())) return;
    if (kind === 'silent' && !commands.value.canDeclareSilent) return;
    if (kind === 'bet' && !commands.value.allowedBets.includes(payload.bet?.action)) return;
    return perform({ type: 'command', contextKey: commands.value.contextKey, body: {
      request_id: crypto.randomUUID(), round_id: liveState.value.round_id, basis_event: liveState.value.event_id,
      kind, ...payload,
    } });
  }
  function startRound() {
    if (canStartNextRound.value) return perform({ type: 'start', body: { round_id: liveState.value.round_id + 1 } });
  }
  function releaseSpeech() {
    if (commands.value.canRelease && !commands.value.disabled) return perform({ type: 'release',
      speechRequestId: liveState.value.deferred_speech.request_id, body: {
      round_id: liveState.value.round_id, basis_event: liveState.value.event_id,
    } });
  }
  function closeGame() { if (context) return perform({ type: 'close' }); }
  function retryLastCommand() { if (retryRecord) return perform(retryRecord); }
  async function readHistory(roundId) {
    if (!context || disposed) throw new defaultApi.ApiError('테이블이 닫혔습니다.', 404);
    const g = generation, requestContext = { ...context };
    try {
      const result = roundId == null ? await replayApi.listReplayRounds(requestContext)
        : await replayApi.getReplayFrames(requestContext, roundId);
      if (!current(g)) throw new Error('테이블이 바뀌었습니다.');
      return result;
    } catch (failure) {
      if (current(g)) {
        if ([401, 403].includes(failure.status) || failure.status === 404 && roundId == null) markClosed('기록을 조회할 테이블이 없습니다.');
        else if (failure.status === 404) await refresh(); // 라운드 없음과 테이블 삭제를 구분한다.
      }
      throw failure;
    }
  }
  function dispose() { disposed = true; generation += 1; detach(); }
  if (getCurrentScope()) onScopeDispose(dispose);
  return { liveState, view, entries, commands, sessionSummary, connection, health, error, pending, restoring,
    participation, remaining, canCreate, canStartNextRound, canRetry: uncertain,
    deferredSpeech: computed(() => liveState.value?.deferred_speech || null),
    initialize, reconnect, createGame, refresh, startRound, speak: payload => command('speak', payload),
    silent: () => command('silent'), bet: bet => command('bet', { bet }), releaseSpeech,
    closeGame, retryLastCommand, dispose,
    listReplayRounds: () => readHistory(), getReplayFrames: roundId => readHistory(roundId) };
}
