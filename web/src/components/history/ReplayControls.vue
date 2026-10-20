<script setup>
// 완료 라운드 선택, 처음/이전/다음/마지막, 슬라이더와 현재로 돌아가기.

// 세부 계약: web/UI_SPEC.md. 화면/동작은 후속 구현한다.
defineProps({
  rounds: { type: Array, default: () => [] },
  selectedRoundId: { type: Number, default: null },
  selectedFrameId: { type: String, default: null },
  position: { type: Number, default: 0 },
  frameCount: { type: Number, default: 0 },
  loading: { type: Boolean, default: false },
  error: { type: String, default: null },
});

defineEmits(['select-round', 'step-frame', 'seek-position', 'return-live']);
</script>

<template>
  <section class="skeleton-panel" aria-label="기록 탐색 영역">
    라운드 · 사건 탐색 영역
    <!-- TODO: 처음/이전/다음/마지막, 슬라이더, 라운드 선택, 현재로 돌아가기 -->
  </section>
</template>

<style scoped>
/* TODO: 이 컴포넌트의 스타일. 공통 데스크톱 배치는 styles/layout.css. */
</style>
