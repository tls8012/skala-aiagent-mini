<script setup>
import { computed } from 'vue';
const props = defineProps({
  allowedBets: { type: Array, default: () => [] },
  raiseOptions: { type: Object, default: () => ({}) },
  disabled: { type: Boolean, default: false },
});
const emit = defineEmits(['bet']);
const actions = [{ action: 'check', label: '체크' }, { action: 'call', label: '콜' }, { action: 'fold', label: '폴드' }];
const raises = computed(() => [
  { size: 'maximum', label: '최대' }, { size: 'large', label: '큰' },
  { size: 'medium', label: '중간' }, { size: 'small', label: '작은' },
].map(item => ({ ...item, amount: props.raiseOptions[item.size] })));
function allowed(action, size) {
  if (props.disabled || !props.allowedBets.includes(action)) return false;
  return action !== 'raise' || (Number.isSafeInteger(props.raiseOptions[size]) && props.raiseOptions[size] > 0);
}
function bet(action, size) {
  if (allowed(action, size)) emit('bet', action === 'raise' ? { action, raise_size: size } : { action });
}
</script>

<template>
  <section class="betting-controls" aria-label="베팅 조작 영역">
    <div class="betting-controls__heading"><h3>베팅</h3><span v-if="!allowedBets.length">지금은 베팅할 수 없어요.</span></div>
    <div class="betting-controls__buttons">
      <button v-for="item in actions" :key="item.action" type="button" :class="{ 'betting-controls__fold': item.action === 'fold' }" :disabled="!allowed(item.action)" @click="bet(item.action)">{{ item.label }}</button>
      <span class="betting-controls__divider" aria-hidden="true" />
      <button v-for="item in raises" :key="item.size" type="button" class="betting-controls__raise" :disabled="!allowed('raise', item.size)" @click="bet('raise', item.size)">
        {{ item.label }} 레이즈 <small>{{ allowedBets.includes('raise') && Number.isSafeInteger(item.amount) && item.amount > 0 ? `+${item.amount}` : '—' }}</small>
      </button>
    </div>
  </section>
</template>

<style scoped>
.betting-controls { padding-top: 16px; border-top: 1px solid #e8ebf0; }
.betting-controls__heading { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.betting-controls h3 { margin: 0; color: #6c7689; font-size: 12px; font-weight: 600; }
.betting-controls__heading > span { color: #969eac; font-size: 11px; }
.betting-controls__buttons { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.betting-controls button { padding: 10px 16px; border: 1px solid #dce2ec; border-radius: 10px; background: #fff; color: #5f6e87; font: inherit; font-size: 12px; cursor: pointer; }
.betting-controls .betting-controls__fold { color: #a36870; }
.betting-controls .betting-controls__raise { background: #f0f3f9; }
.betting-controls button small { margin-left: 8px; color: #7d8ea9; font-size: 11px; }
.betting-controls button:disabled { opacity: .4; cursor: default; }
.betting-controls button:hover:enabled { border-color: #9aacc9; }
.betting-controls button:focus-visible { outline: 2px solid #6878ac; outline-offset: 3px; }
.betting-controls__divider { width: 1px; height: 22px; margin: 0 5px; background: #e0e5ed; }
</style>
