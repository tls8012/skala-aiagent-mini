<script setup>
defineProps({ mode: { type: String, default: 'turn' }, notice: { type: String, default: '' } });
defineEmits(['update:mode']);
</script>

<template>
  <div class="preview-tools">
    <div class="preview-tools__row">
      <span>화면 미리보기 · 실제 게임과 LLM은 실행하지 않습니다.</span>
      <label>입력 상태 예시
        <select :value="mode" @change="$emit('update:mode', $event.target.value)">
          <option value="turn">내 베팅 차례</option>
          <option value="folded">폴드 후 대화</option>
          <option value="exhausted">발언 소진</option>
          <option value="deferred">보류 발언</option>
          <option value="ready">다음 라운드 대기</option>
        </select>
      </label>
    </div>
    <p role="status">{{ notice }}</p>
  </div>
</template>

<style scoped>
.preview-tools { padding: 12px 14px; border-radius: 10px; background: #f2f4f8; color: #8a94a6; font-size: 11px; }
.preview-tools__row { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.preview-tools label { display: flex; gap: 9px; align-items: center; }
.preview-tools select { padding: 5px 8px; border: 1px solid #dce1eb; border-radius: 7px; background: #fff; color: #71819c; font: inherit; }
.preview-tools select:focus-visible { outline: 2px solid #6878ac; outline-offset: 2px; }
.preview-tools p { margin: 8px 0 0; color: #71819c; overflow-wrap: anywhere; }
</style>
