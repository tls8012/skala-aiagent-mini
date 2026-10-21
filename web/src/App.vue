<script setup>
import LiveGame from './views/LiveGame.vue';
import GamePreview from './views/GamePreview.vue';

// 기존 컴포넌트 예시는 ?preview=1에서 계속 확인할 수 있다.
const preview = typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('preview') === '1';
</script>

<template>
  <GamePreview v-if="preview" />
  <LiveGame v-else />
</template>
