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

defineEmits(['start-round', 'close-game', 'speak', 'silent', 'release-speech', 'bet', 'seek-frame', 'step-frame', 'seek-position', 'select-round', 'return-live']);
</script>

<template>
  <main class="game-screen">
    <GameToolbar :session-summary="sessionSummary" :connection="connection" :read-only="effectiveReadOnly" :can-start-next-round="canStartNextRound"
      @start-round="$emit('start-round')" @close-game="$emit('close-game')" @return-live="$emit('return-live')">
      <slot name="setup" />
    </GameToolbar>
    <slot name="notice" />
    <div class="game-screen__body">
      <PokerTable :ai-seats="view.aiSeats || []" :human-seat="view.humanSeat || null" :summary="view.summary || {}" :read-only="effectiveReadOnly" :motion-enabled="replay.mode !== 'replay'" @expand-speech="expandSpeech" />
      <EventLog ref="eventLog" :entries="entries" :selected-event-id="expandedEventId" :replay-available="false" />
    </div>
    <HumanControls :participation="participation" :commands="commands" :remaining-utterances="remainingUtterances" :read-only="effectiveReadOnly" :deferred-speech="deferredSpeech"
      @speak="$emit('speak', $event)" @silent="$emit('silent')" @release-speech="$emit('release-speech')" @bet="$emit('bet', $event)" />
    <ReplayControls :rounds="[]" :position="0" :frame-count="0" :loading="false" />
    <!-- 정확한 과거 프레임은 백엔드 history API와 useReplay 구현 이후 연결한다. -->
  </main>
</template>

<style scoped>
.game-screen { display: grid; gap: 16px; padding: 24px; }
.game-screen__body { display: grid; grid-template-columns: minmax(900px, 1fr) 320px; gap: 24px; }
/* TODO: 실제 시각 디자인. */
</style>
