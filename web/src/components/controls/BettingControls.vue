<script setup>
// 서버가 준 행동과 최대/큰/중간/작은 레이즈 선택지를 표시한다.

// 세부 계약: web/UI_SPEC.md. 화면/동작은 후속 구현한다.
defineProps({
  allowedBets: { type: Array, default: () => [] },
  raiseOptions: { type: Object, default: () => ({}) },
  disabled: { type: Boolean, default: false },
});

defineEmits(['bet']);
</script>

<template>
  <section aria-label="베팅 조작 영역">
    베팅 조작 영역
    <!-- TODO: allowedBets와 raiseOptions만 사용. 숫자 계산과 API 호출은 하지 않는다. -->
  </section>
</template>

<style scoped>
/* TODO: 이 컴포넌트의 스타일. 공통 데스크톱 배치는 styles/layout.css. */
</style>
