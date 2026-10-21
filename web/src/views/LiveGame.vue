<script setup>
import { computed, onMounted } from 'vue';
import GameScreen from './GameScreen.vue';
import GameSetup from '../components/GameSetup.vue';
import SessionNotice from '../components/SessionNotice.vue';
import { useGameSession } from '../composables/useGameSession.js';
import { useReplay } from '../composables/useReplay.js';

const session = useGameSession();
const replay = useReplay(session);
const { commands, sessionSummary, connection, health, error, pending, restoring, canCreate,
  canStartNextRound, canRetry, deferredSpeech, initialize, reconnect,
  createGame, startRound, speak, silent, bet, releaseSpeech, closeGame, retryLastCommand } = session;
const isReplay = computed(() => replay.mode.value === 'replay');
const replayState = replay.state;
const displayView = computed(() => isReplay.value ? replay.displayView.value || session.view.value : session.view.value);
const displayEntries = computed(() => isReplay.value && replay.selectedFrame.value ? replay.entries.value : session.entries.value);
const displayRemaining = computed(() => isReplay.value && replay.selectedFrame.value
  ? replay.selectedFrame.value.snapshot.human_display.remaining_utterances : session.remaining.value);
const displayParticipation = computed(() => isReplay.value && replay.selectedFrame.value
  ? replay.selectedFrame.value.snapshot.human_display.participation : session.participation.value);
const displayCommands = computed(() => isReplay.value
  ? { ...commands.value, disabled: true, allowedBets: [], raiseOptions: {} } : commands.value);
onMounted(initialize);
</script>

<template>
  <GameScreen
    :view="displayView" :entries="displayEntries" :commands="displayCommands" :session-summary="sessionSummary"
    :connection="connection" :participation="displayParticipation" :remaining-utterances="displayRemaining"
    :deferred-speech="isReplay ? null : deferredSpeech" :can-start-next-round="canStartNextRound" :replay="replayState"
    @start-round="replay.runLive(startRound)" @close-game="replay.runLive(closeGame)"
    @speak="replay.runLive(speak, $event)" @silent="replay.runLive(silent)" @bet="replay.runLive(bet, $event)"
    @release-speech="replay.runLive(releaseSpeech)" @seek-frame="replay.seekFrame"
    @select-round="replay.selectRound" @step-frame="replay.step" @seek-position="replay.seekPosition"
    @return-live="replay.returnLive" @retry-replay="replay.retry"
  >
    <template #setup><GameSetup :busy="pending && (!sessionSummary.gameId || sessionSummary.status === 'closed')" :disabled="!canCreate || isReplay" @create-game="replay.runLive(createGame, $event)" /></template>
    <template #notice>
      <SessionNotice :health="health" :error="error" :busy="pending" :restoring="restoring" :can-retry="canRetry" :read-only="isReplay"
        @reconnect="reconnect" @retry-command="replay.runLive(retryLastCommand)" />
    </template>
  </GameScreen>
</template>
