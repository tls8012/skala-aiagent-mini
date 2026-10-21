<script setup>
import GameScreen from './views/GameScreen.vue';
import GameSetup from './components/GameSetup.vue';
import PreviewTools from './preview/PreviewTools.vue';
import { useTablePreview } from './preview/useTablePreview.js';

const { mode, notice, view, entries, commands, deferredSpeech, sessionSummary, participation,
  remaining, canStartNextRound, applySettings, recordAction, recordBet } = useTablePreview();
</script>

<template>
  <GameScreen
    :view="view" :entries="entries" :commands="commands" :deferred-speech="deferredSpeech"
    :session-summary="sessionSummary" :participation="participation" :remaining-utterances="remaining"
    :can-start-next-round="canStartNextRound" :connection="{ status: 'idle' }"
    @start-round="recordAction('라운드 시작')" @close-game="recordAction('테이블 종료')"
    @speak="recordAction(`발언: ${$event.text}`)" @silent="recordAction('발언 없음')"
    @release-speech="recordAction('보류 발언 공개')"
    @bet="recordBet"
  >
    <template #setup><GameSetup @create-game="applySettings" /></template>
    <template #preview><PreviewTools v-model:mode="mode" :notice="notice" /></template>
  </GameScreen>
</template>
