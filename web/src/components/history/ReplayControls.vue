<script setup>
import { computed } from 'vue';
const props = defineProps({
  rounds: { type: Array, default: () => [] }, mode: { type: String, default: 'live' },
  selectedRoundId: { type: Number, default: null }, selectedFrameId: { type: String, default: null },
  position: { type: Number, default: 0 }, frameCount: { type: Number, default: 0 },
  loading: { type: Boolean, default: false }, error: { type: String, default: null },
});
defineEmits(['select-round', 'step-frame', 'seek-position', 'return-live', 'retry']);
const navigable = computed(() => props.mode === 'replay' && props.frameCount > 0 && !props.loading);
</script>

<template>
  <section class="replay-controls" aria-label="기록 탐색 영역" :aria-busy="loading">
    <div class="replay-controls__row">
      <label>현재 게임의 기록
        <select :value="selectedRoundId ?? ''" :disabled="!rounds.length" @change="$emit('select-round', Number($event.target.value))">
          <option value="" disabled>라운드 선택</option>
          <option v-for="round in rounds" :key="round.round_id" :value="round.round_id">라운드 {{ round.round_id }} · {{ round.completed ? '완료' : '진행 중' }} ({{ round.frame_count }}개)</option>
        </select>
      </label>
      <div class="replay-controls__steps">
        <button type="button" :disabled="!navigable || position === 0" @click="$emit('step-frame', 'first')">처음</button>
        <button type="button" :disabled="!navigable || position === 0" @click="$emit('step-frame', 'previous')">이전</button>
        <span>{{ frameCount ? `${position + 1} / ${frameCount}` : '—' }}</span>
        <button type="button" :disabled="!navigable || position >= frameCount - 1" @click="$emit('step-frame', 'next')">다음</button>
        <button type="button" :disabled="!navigable || position >= frameCount - 1" @click="$emit('step-frame', 'last')">마지막</button>
      </div>
      <button type="button" :disabled="mode !== 'replay'" @click="$emit('return-live')">현재로 돌아가기</button>
    </div>
    <input class="replay-controls__slider" type="range" aria-label="선택 라운드의 사건 위치" min="0" :max="Math.max(0, frameCount - 1)" :value="position" :disabled="!navigable" @input="$emit('seek-position', Number($event.target.value))" />
    <p v-if="loading" role="status">기록을 불러오고 있어요. 게임은 계속 진행 중이에요.</p>
    <p v-else-if="!rounds.length">첫 라운드가 시작되면 기록을 선택할 수 있어요.</p>
    <p v-else-if="mode === 'live'">대화나 사건 기록을 클릭하면 그때 테이블을 볼 수 있어요.</p>
    <div v-if="error" class="replay-controls__error"><span role="alert">{{ error }}</span><button type="button" :disabled="loading" @click="$emit('retry')">다시 불러오기</button></div>
  </section>
</template>

<style scoped>
.replay-controls { padding: 18px 24px; border: 1px solid #e0e3e9; border-radius: 16px; background: #ffffffb3; color: #78859a; font-size: 12px; }
.replay-controls__row { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.replay-controls label, .replay-controls__steps { display: flex; align-items: center; gap: 8px; }
.replay-controls button, .replay-controls select { padding: 7px 11px; border: 1px solid #d6deeb; border-radius: 8px; background: #fff; color: #617696; font: inherit; cursor: pointer; }
.replay-controls button:disabled, .replay-controls select:disabled { opacity: .4; cursor: default; }
.replay-controls button:focus-visible, .replay-controls select:focus-visible, .replay-controls input:focus-visible { outline: 2px solid #6878ac; outline-offset: 3px; }
.replay-controls__steps > span { min-width: 64px; text-align: center; font-size: 11px; }
.replay-controls__slider { width: 100%; margin: 16px 0 0; accent-color: #7c94ba; }
.replay-controls > p { margin: 9px 0 0; color: #949eac; font-size: 11px; }
.replay-controls__error { display: flex; gap: 12px; align-items: center; margin-top: 12px; color: #ae4853; }
</style>
