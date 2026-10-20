<script setup>
// 게임/라운드 상태, 연결 상태, 다음 라운드 시작과 종료.

// 세부 계약: web/UI_SPEC.md. 화면/동작은 후속 구현한다.
defineProps({
  sessionSummary: { type: Object, default: () => ({}) },
  connection: { type: Object, default: () => ({}) },
  readOnly: { type: Boolean, default: false },
  canStartNextRound: { type: Boolean, default: false },
});

defineEmits(['start-round', 'close-game', 'return-live']);
</script>

<template>
  <header class="skeleton-panel game-toolbar">
    <h1>인디언 포커</h1>
    <slot />
    <!-- TODO: 라운드 상태, 연결 상태, 다음 라운드 시작/종료 -->
  </header>
</template>

<style scoped>
.game-toolbar h1 { margin-top: 0; }
/* TODO: 실제 시각 디자인. */
</style>
