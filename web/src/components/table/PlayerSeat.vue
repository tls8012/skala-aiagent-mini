<script setup>
// 캐릭터, 카드, 말풍선, 이름, 칩, 확정 행동을 하나의 좌석으로 묶는다.
import { computed } from 'vue';
import SlimeAvatar from './SlimeAvatar.vue';
import PlayingCard from './PlayingCard.vue';
import SpeechBubble from './SpeechBubble.vue';

const props = defineProps({
  seat: { type: Object, default: null },
  readOnly: { type: Boolean, default: false },
  motionEnabled: { type: Boolean, default: true },
});

defineEmits(['expand-speech']);

const stateLabel = computed(() => ({
  folded: '폴드', eliminated: '탈락', failed: '응답 오류', spectating: '관전',
})[props.seat?.state] || null);
const card = computed(() => props.seat?.state === 'folded'
  ? { visibility: 'back' } : props.seat?.card || { visibility: 'absent' });
</script>

<template>
  <section class="player-seat" :class="{ 'player-seat--inactive': ['folded', 'eliminated', 'failed'].includes(seat?.state) }" :aria-label="`${seat?.label || '플레이어'} 좌석`">
    <SpeechBubble :speech="seat?.speech || null" @expand-speech="$emit('expand-speech', $event)" />
    <div class="player-seat__character">
      <div class="player-seat__card"><PlayingCard :card="card" /></div>
      <SlimeAvatar
        :color="seat?.color || ''"
        :state="seat?.state || 'spectating'"
        :motion-enabled="motionEnabled"
        :gaze-angle="seat?.gazeAngle ?? -45"
        :gaze-strength="seat?.gazeStrength ?? 1"
      />
    </div>
    <div class="player-seat__identity">
      <strong>{{ seat?.label || '나' }}</strong>
      <span v-if="seat?.isBettingTurn" class="player-seat__turn">베팅 차례</span>
      <span v-if="stateLabel" class="player-seat__state">{{ stateLabel }}</span>
    </div>
    <div class="player-seat__details">
      <span v-if="Number.isFinite(seat?.chips)">칩 <b>{{ seat.chips }}</b></span>
      <span v-if="seat?.lastActionLabel">{{ seat.lastActionLabel }}</span>
    </div>
  </section>
</template>

<style scoped>
.player-seat { width: 260px; max-width: 100%; text-align: center; justify-self: center; }
.player-seat__character { position: relative; width: 176px; margin: 0 auto; }
.player-seat__card { position: absolute; z-index: 1; top: -6px; left: -37px; transform: rotate(-12deg); }
.player-seat--inactive :deep(.slime-avatar) { opacity: .48; }
.player-seat__identity { display: flex; min-height: 30px; margin-top: 10px; gap: 7px; align-items: center; justify-content: center; font-size: 14px; }
.player-seat__turn, .player-seat__state { padding: 3px 7px; border-radius: 20px; background: #e9ebf0; color: #6c7282; font-size: 11px; }
.player-seat__turn { background: #e4ede3; color: #466c47; }
.player-seat__details { display: flex; height: 22px; gap: 12px; justify-content: center; align-items: center; color: #737b89; font-size: 12px; }
.player-seat__details b { color: #383e4a; }
</style>
