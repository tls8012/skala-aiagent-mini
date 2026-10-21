<script setup>
import { ref, useId } from 'vue';

const props = defineProps({
  busy: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  error: { type: String, default: null },
});
const emit = defineEmits(['create-game']);
const id = useId();
const aiCount = ref(3);
const humanPlays = ref(true);

function createGame() {
  if (props.busy || props.disabled) return;
  emit('create-game', { aiIds: ['A', 'B', 'C'].slice(0, aiCount.value), humanPlays: humanPlays.value });
}
</script>

<template>
  <form class="game-setup" aria-label="테이블 설정" :aria-busy="busy" @submit.prevent="createGame">
    <fieldset class="game-setup__fields" :disabled="busy || disabled">
      <legend class="game-setup__title">테이블 설정</legend>
      <fieldset class="game-setup__group">
        <legend>AI 플레이어</legend>
        <div class="game-setup__choices">
          <label v-for="count in [2, 3]" :key="count" class="game-setup__option">
            <input v-model="aiCount" type="radio" :name="`${id}-ai-count`" :value="count" />
            <span>{{ count }}명</span>
          </label>
        </div>
      </fieldset>
      <fieldset class="game-setup__group">
        <legend>나의 참여</legend>
        <div class="game-setup__choices">
          <label class="game-setup__option">
            <input v-model="humanPlays" type="radio" :name="`${id}-participation`" :value="true" />
            <span>함께 플레이</span>
          </label>
          <label class="game-setup__option">
            <input v-model="humanPlays" type="radio" :name="`${id}-participation`" :value="false" />
            <span>관전</span>
          </label>
        </div>
      </fieldset>
      <div class="game-setup__submit">
        <span>{{ humanPlays ? '내 자리는 테이블 아래에 있어요.' : '관전 중에도 아래에 함께 앉아요.' }}</span>
        <button type="submit">{{ busy ? '테이블 만드는 중…' : '테이블 만들기' }}</button>
      </div>
    </fieldset>
    <p v-if="error" class="game-setup__error" role="alert">{{ error }}</p>
  </form>
</template>

<style scoped>
.game-setup { padding-top: 18px; border-top: 1px solid #e9ebf0; }
.game-setup__fields { display: flex; gap: 28px; align-items: flex-end; margin: 0; padding: 0; border: 0; }
.game-setup__title { margin-bottom: 14px; padding: 0; color: #666f80; font-size: 12px; font-weight: 600; }
.game-setup__group { min-width: 0; margin: 0; padding: 0; border: 0; }
.game-setup__group legend { margin-bottom: 8px; color: #8991a0; font-size: 11px; }
.game-setup__choices { display: flex; gap: 6px; }
.game-setup__option { position: relative; cursor: pointer; }
.game-setup__option input { position: absolute; inset: 0; width: 100%; height: 100%; margin: 0; opacity: 0; cursor: inherit; }
.game-setup__option span { display: block; padding: 9px 16px; border: 1px solid #e0e4ed; border-radius: 10px; background: #f8f9fb; color: #737c8e; font-size: 12px; }
.game-setup__option input:checked + span { border-color: #b7c4df; background: #edf1fa; color: #475f8c; }
.game-setup__option input:focus-visible + span { outline: 2px solid #6878ac; outline-offset: 3px; }
.game-setup__fields:disabled .game-setup__option { cursor: default; opacity: .55; }
.game-setup__submit { display: flex; flex: 1; align-items: center; justify-content: flex-end; gap: 18px; }
.game-setup__submit > span { color: #9098a6; font-size: 11px; }
.game-setup__submit button { padding: 10px 19px; border: 1px solid #556586; border-radius: 10px; background: #556586; color: #fff; font: inherit; font-size: 12px; cursor: pointer; }
.game-setup__submit button:hover:enabled { background: #455678; }
.game-setup__submit button:disabled { opacity: .45; cursor: default; }
.game-setup__submit button:focus-visible { outline: 2px solid #6878ac; outline-offset: 3px; }
.game-setup__error { margin: 12px 0 0; color: #ae4853; font-size: 12px; }
</style>
