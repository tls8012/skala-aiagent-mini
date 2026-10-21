<script setup>
import { computed, ref, useId, watch } from 'vue';
import BettingControls from './BettingControls.vue';

const props = defineProps({
  participation: { type: String, default: 'spectating' },
  commands: { type: Object, default: () => ({}) },
  remainingUtterances: { type: Number, default: 0 },
  deferredSpeech: { type: Object, default: null },
  readOnly: { type: Boolean, default: false },
});
const emit = defineEmits(['speak', 'silent', 'release-speech', 'bet']);
const id = useId();
const draft = ref('');
const blocked = computed(() => props.readOnly || props.participation !== 'playing'
  || props.commands.disabled || props.commands.pending);
const canSpeak = computed(() => !blocked.value && props.commands.canSpeak === true
  && props.remainingUtterances > 0 && !props.deferredSpeech);
const withinLimit = computed(() => !props.commands.maxMessageChars || draft.value.length <= props.commands.maxMessageChars);
const canSend = computed(() => canSpeak.value && Boolean(draft.value.trim()) && withinLimit.value);
const canSilent = computed(() => !blocked.value && props.commands.canDeclareSilent === true && !props.commands.isSilent);
const canRelease = computed(() => !blocked.value && props.commands.canRelease === true && Boolean(props.deferredSpeech));
const hint = computed(() => props.readOnly ? '기록을 보는 동안에는 입력할 수 없어요.' :
  props.participation !== 'playing' ? '관전 중이에요. 아래 자리에서 테이블을 지켜볼 수 있어요.' :
  props.commands.pending ? '입력을 보내는 중이에요…' : props.commands.disabledReason ||
  (props.remainingUtterances <= 0 ? '이번 라운드의 발언 기회를 모두 사용했어요. 베팅은 별도로 할 수 있어요.' :
    props.commands.isSilent ? '발언 없음 상태예요. 새 발언으로 다시 참여할 수 있어요.' : '잡담도 발언 횟수에 포함돼요.'));
function speak() {
  if (canSend.value) emit('speak', { text: draft.value.trim() });
}
function onKeydown(event) {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.isComposing) {
    event.preventDefault();
    speak();
  }
}
function silent() {
  if (canSilent.value) emit('silent');
}
function release() {
  if (canRelease.value) emit('release-speech');
}
function bet(value) {
  if (!blocked.value) emit('bet', value);
}
// 네트워크 계층이 접수 성공을 알려 준 경우에만 전송한 초안을 비운다.
watch(() => props.commands.contextKey, () => { draft.value = ''; });
watch(() => props.commands.speechReceipt, receipt => {
  if (receipt && receipt.contextKey === props.commands.contextKey
    && ['accepted', 'deferred'].includes(receipt.status) && draft.value.trim() === receipt.text) draft.value = '';
});
</script>

<template>
  <section class="human-controls" aria-label="인간 입력 영역" :aria-busy="Boolean(commands.pending)">
    <div class="human-controls__heading"><h2>내 자리</h2><span>남은 발언 <b>{{ remainingUtterances }}</b>회</span></div>
    <form class="human-controls__speech" @submit.prevent="speak">
      <label :for="`${id}-speech`">테이블에 말하기</label>
      <textarea :id="`${id}-speech`" v-model="draft" rows="2" placeholder="누구에게든 말을 걸어 보세요." :maxlength="commands.maxMessageChars || undefined" :disabled="!canSpeak" :aria-describedby="`${id}-hint`" @keydown="onKeydown" />
      <div class="human-controls__speech-actions">
        <p :id="`${id}-hint`">{{ hint }}</p>
        <span v-if="commands.maxMessageChars" class="human-controls__length">{{ draft.length }} / {{ commands.maxMessageChars }}</span>
        <button type="button" :disabled="!canSilent" @click="silent">{{ commands.isSilent ? '발언 없음 선언됨' : '발언 없음' }}</button>
        <button type="submit" class="human-controls__send" :disabled="!canSend">발언 보내기</button>
      </div>
    </form>
    <div v-if="deferredSpeech" class="human-controls__deferred">
      <div><strong>공개를 기다리는 발언</strong><p>{{ deferredSpeech.text }}</p><small>다른 참가자의 발언 이후, 현재 상황을 보고 다시 확인해 주세요.</small></div>
      <button type="button" :disabled="!canRelease" @click="release">이 발언 공개하기</button>
    </div>
    <p v-if="commands.error" class="human-controls__error" role="alert">{{ commands.error }}</p>
    <BettingControls :allowed-bets="commands.allowedBets || []" :raise-options="commands.raiseOptions || {}" :disabled="Boolean(blocked)" @bet="bet" />
  </section>
</template>

<style scoped>
.human-controls { display: grid; gap: 16px; padding: 20px 24px; border: 1px solid #e0e3e9; border-radius: 20px; background: #ffffffb3; }
.human-controls__heading { display: flex; align-items: center; justify-content: space-between; }
.human-controls h2 { margin: 0; color: #59667d; font-size: 14px; }
.human-controls__heading > span { color: #8c95a4; font-size: 11px; }
.human-controls__heading b { color: #526a90; }
.human-controls__speech { display: grid; gap: 9px; }
.human-controls__speech > label { color: #768196; font-size: 12px; }
.human-controls textarea { width: 100%; min-height: 78px; resize: vertical; padding: 12px 14px; border: 1px solid #dce2ec; border-radius: 12px; background: #fff; color: #4a566b; font: inherit; font-size: 13px; line-height: 1.7; }
.human-controls textarea::placeholder { color: #adb4c0; }
.human-controls textarea:disabled { background: #f5f6f9; opacity: .7; }
.human-controls textarea:focus-visible { outline: 2px solid #9aaed2; outline-offset: 1px; }
.human-controls__speech-actions { display: flex; gap: 8px; align-items: center; }
.human-controls__speech-actions > p { flex: 1; margin: 0; color: #929ba9; font-size: 11px; }
.human-controls__length { margin-right: 8px; color: #9da5b2; font-size: 10px; }
.human-controls button { padding: 9px 14px; border: 1px solid #dce2ec; border-radius: 9px; background: #fff; color: #67758e; font: inherit; font-size: 12px; cursor: pointer; }
.human-controls .human-controls__send { border-color: #556586; background: #556586; color: #fff; }
.human-controls button:disabled { opacity: .4; cursor: default; }
.human-controls button:focus-visible { outline: 2px solid #6878ac; outline-offset: 3px; }
.human-controls__deferred { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 14px 16px; border-radius: 12px; background: #f5f0e7; }
.human-controls__deferred strong { color: #8e7a52; font-size: 12px; }
.human-controls__deferred p { max-height: 80px; overflow-y: auto; margin: 7px 0; color: #7a705e; font-size: 13px; white-space: pre-wrap; overflow-wrap: anywhere; }
.human-controls__deferred small { color: #a19681; font-size: 10px; }
.human-controls__error { margin: 0; color: #ae4853; font-size: 12px; }
</style>
