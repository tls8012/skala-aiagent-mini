<script setup>
// 하단 인간 입력, 발언 없음, 보류 발언 재확인. 관전/리플레이는 읽기 전용.
import BettingControls from './BettingControls.vue';

// 세부 계약: web/UI_SPEC.md. 화면/동작은 후속 구현한다.
defineProps({
  participation: { type: String, default: '' },
  commands: { type: Object, default: () => ({}) },
  remainingUtterances: { type: Number, default: 0 },
  deferredSpeech: { type: Object, default: null },
  readOnly: { type: Boolean, default: false },
});

defineEmits(['speak', 'silent', 'release-speech', 'bet']);
</script>

<template>
  <section class="skeleton-panel" aria-label="인간 입력 영역">
    <p>인간 입력 · 발언 없음 · 보류 발언 확인 / 관전 시 읽기 전용</p>
    <BettingControls :allowed-bets="commands.allowedBets || []" :raise-options="commands.raiseOptions || {}" :disabled="true" />
    <!-- TODO: 입력 초안은 로컬로 관리하고 speak/silent/release-speech 의도만 전달 -->
  </section>
</template>

<style scoped>
/* TODO: 이 컴포넌트의 스타일. 공통 데스크톱 배치는 styles/layout.css. */
</style>
