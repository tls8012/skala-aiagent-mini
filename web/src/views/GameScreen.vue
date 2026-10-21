<script setup>
// 전체 화면 조립. live/replay 표시 데이터와 사용자 의도를 부모에게 전달한다.
import { computed, ref } from 'vue';
import GameToolbar from '../components/GameToolbar.vue';
import PokerTable from '../components/table/PokerTable.vue';
import HumanControls from '../components/controls/HumanControls.vue';
import EventLog from '../components/history/EventLog.vue';
import ReplayControls from '../components/history/ReplayControls.vue';

const props = defineProps({
  sessionSummary: { type: Object, default: () => ({}) },
  view: { type: Object, default: () => ({}) },
  connection: { type: Object, default: () => ({}) },
  commands: { type: Object, default: () => ({}) },
  replay: { type: Object, default: () => ({}) },
  entries: { type: Array, default: () => [] },
  participation: { type: String, default: 'spectating' },
  remainingUtterances: { type: Number, default: 0 },
  deferredSpeech: { type: Object, default: null },
  readOnly: { type: Boolean, default: false },
  canStartNextRound: { type: Boolean, default: false },
});
const effectiveReadOnly = computed(() => props.readOnly || props.replay.mode === 'replay');

const expandedEventId = ref(null);
const eventLog = ref(null);
function expandSpeech(eventId) {
  expandedEventId.value = eventId;
  eventLog.value?.focusEvent(eventId);
}

defineEmits(['start-round', 'close-game', 'speak', 'silent', 'release-speech', 'bet', 'seek-frame', 'step-frame', 'seek-position', 'select-round', 'return-live', 'retry-replay']);
</script>

<template>
  <main class="game-screen">
    <GameToolbar :session-summary="sessionSummary" :connection="connection" :read-only="effectiveReadOnly" :can-start-next-round="canStartNextRound"
      @start-round="$emit('start-round')" @close-game="$emit('close-game')" @return-live="$emit('return-live')">
      <slot name="setup" />
    </GameToolbar>
    <slot name="notice" />
    <div v-if="replay.mode === 'replay'" class="game-screen__replay-banner" role="status">
      <strong>{{ replay.selectedEventId == null ? '과거 기록 불러오는 중' : `과거 기록 · 라운드 ${replay.selectedRoundId} · 사건 #${replay.selectedEventId}` }}</strong>
      <span>입력 중지 · 실제 게임은 계속 진행됩니다. {{ replay.unreadCount ? `현재에 새 사건 ${replay.unreadCount}개` : '' }}</span>
    </div>
    <div class="game-screen__body">
      <PokerTable :ai-seats="view.aiSeats || []" :human-seat="view.humanSeat || null" :summary="view.summary || {}" :read-only="effectiveReadOnly" :motion-enabled="replay.mode !== 'replay'" @expand-speech="expandSpeech" />
      <EventLog ref="eventLog" :entries="entries" :selected-event-id="replay.mode === 'replay' ? replay.selectedEventId : expandedEventId"
        :selected-frame-id="replay.selectedFrameId" :replay-available="Boolean(replay.available)" @seek-frame="$emit('seek-frame', $event)" />
    </div>
    <HumanControls :participation="participation" :commands="commands" :remaining-utterances="remainingUtterances" :read-only="effectiveReadOnly" :deferred-speech="deferredSpeech"
      @speak="$emit('speak', $event)" @silent="$emit('silent')" @release-speech="$emit('release-speech')" @bet="$emit('bet', $event)" />
    <ReplayControls :rounds="replay.rounds || []" :mode="replay.mode || 'live'" :selected-round-id="replay.selectedRoundId"
      :selected-frame-id="replay.selectedFrameId" :position="replay.position || 0" :frame-count="replay.frameCount || 0" :loading="Boolean(replay.loading)" :error="replay.error || null"
      @select-round="$emit('select-round', $event)" @step-frame="$emit('step-frame', $event)" @seek-position="$emit('seek-position', $event)"
      @return-live="$emit('return-live')" @retry="$emit('retry-replay')" />
  </main>
</template>

<style scoped>
.game-screen { display: grid; gap: 16px; padding: 24px; }
.game-screen__body { display: grid; grid-template-columns: minmax(900px, 1fr) 320px; gap: 24px; }
.game-screen__replay-banner { display: flex; justify-content: space-between; align-items: center; padding: 12px 18px; border: 1px solid #e6d6b5; border-radius: 12px; background: #faf3e6; color: #9a7c45; font-size: 12px; }
.game-screen__replay-banner > span { font-size: 11px; }
/* TODO: 실제 시각 디자인. */
</style>
