<script setup>
import { onMounted } from 'vue';
import GameScreen from './GameScreen.vue';
import GameSetup from '../components/GameSetup.vue';
import SessionNotice from '../components/SessionNotice.vue';
import { useGameSession } from '../composables/useGameSession.js';

const { view, entries, commands, sessionSummary, connection, health, error, pending, restoring,
  participation, remaining, canCreate, canStartNextRound, canRetry, deferredSpeech,
  initialize, reconnect, createGame, startRound, speak, silent, bet, releaseSpeech, closeGame, retryLastCommand } = useGameSession();
onMounted(initialize);
</script>

<template>
  <GameScreen
    :view="view" :entries="entries" :commands="commands" :session-summary="sessionSummary"
    :connection="connection" :participation="participation" :remaining-utterances="remaining"
    :deferred-speech="deferredSpeech" :can-start-next-round="canStartNextRound"
    @start-round="startRound" @close-game="closeGame" @speak="speak" @silent="silent" @bet="bet" @release-speech="releaseSpeech"
  >
    <template #setup><GameSetup :busy="pending && (!sessionSummary.gameId || sessionSummary.status === 'closed')" :disabled="!canCreate" @create-game="createGame" /></template>
    <template #notice>
      <SessionNotice :health="health" :error="error" :busy="pending" :restoring="restoring" :can-retry="canRetry"
        @reconnect="reconnect" @retry-command="retryLastCommand" />
    </template>
  </GameScreen>
</template>
