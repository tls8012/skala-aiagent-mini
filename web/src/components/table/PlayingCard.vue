<script setup>
import { computed } from 'vue';

const props = defineProps({
  card: { type: Object, default: () => ({ visibility: 'absent' }) },
});
const suits = {
  S: { symbol: '♠', label: '스페이드', red: false },
  D: { symbol: '♦', label: '다이아', red: true },
  H: { symbol: '♥', label: '하트', red: true },
  C: { symbol: '♣', label: '클로버', red: false },
};
// 유효한 앞면만 읽는다. 뒷면에는 숫자/무늬 DOM이나 접근성 라벨을 만들지 않는다.
const face = computed(() => {
  const card = props.card;
  if (card?.visibility !== 'face' || !Number.isInteger(card.rank)
    || card.rank < 2 || card.rank > 14 || !Object.hasOwn(suits, card.suit)) return null;
  return { ...suits[card.suit], rank: ({ 14: 'A', 13: 'K', 12: 'Q', 11: 'J' })[card.rank] || String(card.rank) };
});
</script>

<template>
  <div
    v-if="card && card.visibility !== 'absent'"
    class="playing-card"
    :class="{ 'playing-card--back': !face, 'playing-card--red': face?.red }"
    role="img"
    :aria-label="face ? `${face.label} ${face.rank}` : '비공개 카드'"
  >
    <template v-if="face">
      <span class="playing-card__corner" aria-hidden="true">{{ face.rank }}<small>{{ face.symbol }}</small></span>
      <span class="playing-card__suit" aria-hidden="true">{{ face.symbol }}</span>
      <span class="playing-card__corner playing-card__corner--bottom" aria-hidden="true">{{ face.rank }}<small>{{ face.symbol }}</small></span>
    </template>
    <span v-else class="playing-card__pattern" aria-hidden="true" />
  </div>
</template>

<style scoped>
.playing-card {
  position: relative;
  width: 76px;
  height: 108px;
  flex: 0 0 auto;
  border: 1px solid #d9dce2;
  border-radius: 10px;
  background: #fffefa;
  color: #282c36;
  box-shadow: 0 4px 10px #20252b14;
  user-select: none;
}
.playing-card--red { color: #ce535e; }
.playing-card__corner { position: absolute; top: 7px; left: 8px; display: grid; justify-items: center; font-size: 19px; font-weight: 750; line-height: 1; }
.playing-card__corner small { margin-top: 2px; font-size: 13px; }
.playing-card__corner--bottom { inset: auto 8px 7px auto; transform: rotate(180deg); }
.playing-card__suit { position: absolute; inset: 0; display: grid; place-items: center; font-size: 39px; }
.playing-card--back { padding: 5px; background: #fffefa; }
.playing-card__pattern {
  display: block;
  width: 100%;
  height: 100%;
  border: 3px double #eff0ff;
  border-radius: 5px;
  background: repeating-linear-gradient(45deg, #68708f 0 3px, #7882a2 3px 6px);
}
</style>
