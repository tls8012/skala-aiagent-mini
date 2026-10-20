<script setup>
// AI 수와 인간 참여 여부 선택. 생성 의도만 내보낸다.

// 세부 계약: web/UI_SPEC.md. 화면/동작은 후속 구현한다.
defineProps({
  busy: { type: Boolean, default: false },
  error: { type: String, default: null },
});

defineEmits(['create-game']);
</script>

<template>
  <section aria-label="테이블 설정" class="skeleton-panel">
    AI 인원 · 인간 플레이/관전 설정 영역
    <!-- TODO: 입력과 create-game 의도 전달 -->
  </section>
</template>

<style scoped>
/* TODO: 이 컴포넌트의 스타일. 공통 데스크톱 배치는 styles/layout.css. */
</style>
