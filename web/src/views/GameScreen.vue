<script setup>
// 전체 화면 조립. live/replay 표시 데이터와 사용자 의도를 부모에게 전달한다.
import { ref } from 'vue';
import GameToolbar from '../components/GameToolbar.vue';
import PokerTable from '../components/table/PokerTable.vue';
import HumanControls from '../components/controls/HumanControls.vue';
import EventLog from '../components/history/EventLog.vue';
import ReplayControls from '../components/history/ReplayControls.vue';

// 세부 계약: web/UI_SPEC.md. 화면/동작은 후속 구현한다.
defineProps({
  sessionSummary: { type: Object, default: () => ({}) },
  view: { type: Object, default: () => ({}) },
  connection: { type: Object, default: () => ({}) },
  commands: { type: Object, default: () => ({}) },
  replay: { type: Object, default: () => ({}) },
  entries: { type: Array, default: () => [] },
});

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
    <GameToolbar :session-summary="sessionSummary" :connection="connection" :read-only="true" :can-start-next-round="false">
      <slot name="setup" />
    </GameToolbar>
    <div class="game-screen__body">
      <PokerTable :ai-seats="view.aiSeats || []" :human-seat="view.humanSeat || null" :summary="view.summary || {}" :read-only="true" :motion-enabled="replay.mode !== 'replay'" @expand-speech="expandSpeech" />
      <EventLog ref="eventLog" :entries="entries" :selected-event-id="expandedEventId" :replay-available="false" />
    </div>
    <HumanControls participation="spectating" :commands="commands" :remaining-utterances="0" :read-only="true" />
    <ReplayControls :rounds="[]" :position="0" :frame-count="0" :loading="false" />
    <!-- TODO: 상태 연결은 useGameSession/useReplay에 모으고 자식의 emits를 받는다. -->
  </main>
</template>

<style scoped>
.game-screen { display: grid; gap: 16px; padding: 24px; }
.game-screen__body { display: grid; grid-template-columns: minmax(900px, 1fr) 320px; gap: 24px; }
/* TODO: 실제 시각 디자인. */
</style>
