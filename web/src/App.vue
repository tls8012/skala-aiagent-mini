<script setup>
import GameScreen from './views/GameScreen.vue';
import GameSetup from './components/GameSetup.vue';
import { previewView, previewEntries } from './preview/tablePreview.js';
</script>

<template>
  <GameScreen :view="previewView" :entries="previewEntries">
    <template #setup>
      <GameSetup />
      <p class="preview-notice">화면 미리보기 · 카드와 대화는 예시입니다.</p>
    </template>
  </GameScreen>
</template>

<style scoped>
.preview-notice { margin: 10px 0 0; color: #7c8494; font-size: 12px; }
</style>
