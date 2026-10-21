<script setup>
defineProps({
  health: { type: Object, default: () => ({}) }, error: { type: String, default: null },
  busy: { type: Boolean, default: false }, restoring: { type: Boolean, default: false },
  canRetry: { type: Boolean, default: false },
  readOnly: { type: Boolean, default: false },
});
defineEmits(['reconnect', 'retry-command']);
</script>

<template>
  <section class="session-notice" aria-label="서버와 게임 연결 안내">
    <div class="session-notice__row">
      <p role="status">
        <template v-if="restoring">이전 테이블에 다시 접속하고 있어요.</template>
        <template v-else-if="health.status === 'checking'">서버 상태를 확인하고 있어요.</template>
        <template v-else-if="health.status !== 'ok'">게임 서버에 연결하지 못했어요.</template>
        <template v-else-if="health.backend === 'fake'">시험용 응답으로 실행 중 · 모델 호출 없음</template>
        <template v-else>모델: {{ health.model || 'Ollama' }}</template>
      </p>
      <div>
        <button v-if="canRetry" type="button" :disabled="busy || restoring || readOnly" @click="$emit('retry-command')">같은 요청 다시 확인</button>
        <button type="button" :disabled="busy || restoring" @click="$emit('reconnect')">연결 다시 확인</button>
      </div>
    </div>
    <p v-if="error || health.error" class="session-notice__error" role="alert">{{ error || health.error }}</p>
    <p v-if="canRetry" class="session-notice__hint">응답을 받지 못해 접수 여부를 확인해야 해요. 재확인은 같은 요청을 사용하므로 중복 실행하지 않아요.</p>
  </section>
</template>

<style scoped>
.session-notice { padding: 12px 16px; border: 1px solid #e0e5ed; border-radius: 12px; background: #eef2f7; color: #7b879b; font-size: 12px; }
.session-notice__row { display: flex; justify-content: space-between; gap: 20px; align-items: center; }
.session-notice p { margin: 0; }
.session-notice__row > div { display: flex; gap: 8px; }
.session-notice button { padding: 6px 11px; border: 1px solid #cfd7e5; border-radius: 8px; background: #fff; color: #627593; font: inherit; font-size: 11px; cursor: pointer; }
.session-notice button:disabled { opacity: .45; cursor: default; }
.session-notice button:focus-visible { outline: 2px solid #6878ac; outline-offset: 3px; }
.session-notice .session-notice__error { margin-top: 8px; color: #ae4853; }
.session-notice .session-notice__hint { margin-top: 8px; color: #8c7d5e; }
</style>
