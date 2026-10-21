import { computed, getCurrentScope, onScopeDispose, ref, shallowRef, watch } from 'vue';
import { toTableView } from '../adapters/toTableView.js';
import { toEventEntries } from '../adapters/publicEvents.js';

/** 현재 게임의 메모리 기록만 탐색한다. 실시간 상태/명령/LLM은 변경하지 않는다. */
export function useReplay(session) {
  const mode = ref('live'), rounds = ref([]), selectedFrame = shallowRef(null), heldView = shallowRef(null);
  const loading = ref(false), error = ref(null), revision = ref(0);
  const cache = new Map(), flights = new Map();
  let generation = 0, selection = 0, manifestFlight = null, again = false, retrySelection = null, manifestError = null, entryCursor = 0, disposed = false;
  const gameKey = computed(() => session.liveState.value?.status === 'closed' ? null : session.liveState.value?.game_id || null);
  const current = (g, key) => !disposed && generation === g && gameKey.value === key;
  const frames = computed(() => { revision.value; return cache.get(selectedFrame.value?.round_id) || []; });
  const position = computed(() => frames.value.findIndex(frame => frame.frame_id === selectedFrame.value?.frame_id));
  const displayView = computed(() => selectedFrame.value
    ? toTableView(selectedFrame.value.snapshot, selectedFrame.value.snapshot.display_events) : heldView.value);
  const entries = computed(() => toEventEntries(frames.value.map(frame => ({ ...frame.event, frame_id: frame.frame_id })),
    selectedFrame.value?.snapshot.human_id));
  const unreadCount = computed(() => mode.value === 'replay' ? Math.max(0, (session.liveState.value?.stream_cursor || 0) - entryCursor) : 0);

  function reset() {
    generation += 1; selection += 1; mode.value = 'live'; rounds.value = [];
    selectedFrame.value = null; heldView.value = null; loading.value = false; error.value = null;
    retrySelection = null; manifestError = null; cache.clear(); flights.clear(); manifestFlight = null; again = false; revision.value += 1;
  }
  function enter() {
    if (mode.value === 'live') { heldView.value = session.view.value; entryCursor = session.liveState.value?.stream_cursor || 0; }
    mode.value = 'replay';
  }
  function commit(frame) { selectedFrame.value = frame; error.value = null; }
  async function ensureRound(roundId) {
    if (flights.has(roundId)) return flights.get(roundId);
    const g = generation, key = gameKey.value;
    const promise = (async () => {
      const result = await session.getReplayFrames(roundId);
      if (!current(g, key)) return [];
      if (result.game_id !== key || result.round_id !== roundId || !Array.isArray(result.frames)) throw new Error('기록 응답이 선택한 라운드와 다릅니다.');
      const merged = new Map((cache.get(roundId) || []).map(frame => [frame.frame_id, frame]));
      for (const frame of result.frames) {
        if (!frame.frame_id || frame.game_id !== key || frame.round_id !== roundId
          || frame.snapshot?.game_id !== key || frame.snapshot.round_id !== roundId || frame.snapshot.event_id !== frame.event_id
          || frame.event?.event_id !== frame.event_id || frame.event.round_id !== roundId || !Array.isArray(frame.snapshot.display_events)) throw new Error('올바른 과거 화면이 아닙니다.');
        if (!merged.has(frame.frame_id)) merged.set(frame.frame_id, frame); // 이미 저장한 프레임은 덮어쓰지 않는다.
      }
      const items = [...merged.values()].sort((a, b) => a.event_id - b.event_id);
      cache.set(roundId, items); revision.value += 1;
      return items;
    })().finally(() => { if (flights.get(roundId) === promise) flights.delete(roundId); });
    flights.set(roundId, promise);
    return promise;
  }
  async function refreshRounds() {
    if (!gameKey.value || disposed) return;
    if (manifestFlight) { again = true; return manifestFlight; }
    const g = generation, key = gameKey.value;
    const promise = (async () => {
      do {
        again = false;
        try {
          const result = await session.listReplayRounds();
          if (!current(g, key)) return;
          if (result.game_id !== key || !Array.isArray(result.rounds)) throw new Error('라운드 목록을 읽을 수 없습니다.');
          rounds.value = result.rounds;
          if (error.value === manifestError) error.value = null;
          manifestError = null;
          const active = selectedFrame.value?.round_id;
          const last = cache.get(active)?.at(-1)?.frame_id;
          const meta = result.rounds.find(round => round.round_id === active);
          if (mode.value === 'replay' && meta && meta.last_frame_id !== last) await ensureRound(active);
        } catch (failure) { if (current(g, key)) { manifestError = failure.message; error.value = failure.message; } return; }
      } while (again && current(g, key));
    })().finally(() => { if (manifestFlight === promise) manifestFlight = null; });
    manifestFlight = promise;
    return promise;
  }
  async function choose(roundId, frameId = null) {
    if (!gameKey.value || disposed || !Number.isInteger(roundId) || roundId < 1) return;
    enter();
    const ticket = ++selection, g = generation, key = gameKey.value;
    loading.value = true; error.value = null; retrySelection = () => choose(roundId, frameId);
    try {
      const found = frameId && cache.get(roundId)?.find(frame => frame.frame_id === frameId);
      const items = found ? cache.get(roundId) : await ensureRound(roundId);
      if (!current(g, key) || selection !== ticket) return;
      const frame = frameId ? items.find(item => item.frame_id === frameId) : items[0];
      if (!frame) throw new Error('선택한 사건의 화면을 찾을 수 없습니다.');
      commit(frame);
    } catch (failure) { if (current(g, key) && selection === ticket) error.value = failure.message; }
    finally { if (current(g, key) && selection === ticket) loading.value = false; }
  }
  function seekFrame(id) {
    for (const [roundId, items] of cache) if (items.some(frame => frame.frame_id === id)) return choose(roundId, id);
    const entry = session.entries.value.find(item => item.frameId === id);
    if (entry) return choose(entry.roundId, id);
    if (gameKey.value) { enter(); selection += 1; retrySelection = null; loading.value = false; error.value = '이 사건의 기록 링크를 찾을 수 없습니다.'; }
  }
  function seekPosition(index) {
    if (loading.value || !Number.isInteger(index) || !frames.value[index]) return;
    enter(); selection += 1; commit(frames.value[index]);
    retrySelection = () => choose(selectedFrame.value.round_id, selectedFrame.value.frame_id);
  }
  function step(direction) {
    seekPosition(direction === 'first' ? 0 : direction === 'last' ? frames.value.length - 1
      : direction === 'previous' ? position.value - 1 : direction === 'next' ? position.value + 1 : -1);
  }
  function returnLive() {
    selection += 1; mode.value = 'live'; selectedFrame.value = null; heldView.value = null;
    loading.value = false; error.value = null; retrySelection = null;
  }
  async function retry() { if (retrySelection) await retrySelection(); await refreshRounds(); }
  function runLive(action, ...args) { if (!disposed && mode.value === 'live') return action(...args); }
  const stopKey = watch(gameKey, () => { reset(); if (gameKey.value) void refreshRounds(); }, { immediate: true, flush: 'sync' });
  const stopCursor = watch(() => session.liveState.value?.stream_cursor, () => { void refreshRounds(); });
  function dispose() { disposed = true; stopKey(); stopCursor(); reset(); }
  if (getCurrentScope()) onScopeDispose(dispose);
  const state = computed(() => ({ mode: mode.value, rounds: rounds.value, selectedRoundId: selectedFrame.value?.round_id ?? null,
    selectedFrameId: selectedFrame.value?.frame_id ?? null, selectedEventId: selectedFrame.value?.event_id ?? null,
    position: Math.max(0, position.value), frameCount: frames.value.length, loading: loading.value, error: error.value,
    available: Boolean(gameKey.value), unreadCount: unreadCount.value }));
  return { state, mode, rounds, selectedFrame, displayView, entries, frames, loading, error, position,
    refreshRounds, selectRound: choose, seekFrame, seekPosition, step, returnLive, retry, runLive, dispose };
}
