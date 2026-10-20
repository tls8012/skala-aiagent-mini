<script setup>
// AI 좌석 배치와 중앙 정보. 인간 기준점은 참여/관전 모두 하단 고정.
import PlayerSeat from './PlayerSeat.vue';
import TableStatus from './TableStatus.vue';

defineProps({
  aiSeats: { type: Array, default: () => [] },
  humanSeat: { type: Object, default: null },
  summary: { type: Object, default: () => ({}) },
  readOnly: { type: Boolean, default: false },
  motionEnabled: { type: Boolean, default: true },
});

defineEmits(['expand-speech']);
</script>

<template>
  <section class="poker-table" aria-label="포커 테이블">
    <PlayerSeat
      v-for="(seat, index) in aiSeats"
      :key="seat.key"
      class="poker-table__ai-seat"
      :class="`poker-table__seat--${seat.position || (aiSeats.length === 2 ? ['upper-right', 'upper-left'][index] : ['right', 'top', 'left'][index])}`"
      :seat="seat"
      :read-only="readOnly"
      :motion-enabled="motionEnabled"
      @expand-speech="$emit('expand-speech', $event)"
    />
    <TableStatus class="poker-table__status" :summary="summary" />
    <div class="poker-table__human-anchor">
      <PlayerSeat :seat="humanSeat" :read-only="readOnly" :motion-enabled="motionEnabled" @expand-speech="$emit('expand-speech', $event)" />
    </div>
  </section>
</template>

<style scoped>
.poker-table { position: relative; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); grid-template-rows: 350px 72px 414px; padding: 24px; border: 1px solid #e0e3e9; border-radius: 28px; background: radial-gradient(ellipse at center, #ebeff5 0, #f5f6f8 65%); }
.poker-table__ai-seat { align-self: center; }
.poker-table__seat--right { grid-column: 3; grid-row: 2; }
.poker-table__seat--top { grid-column: 2; grid-row: 1; }
.poker-table__seat--left { grid-column: 1; grid-row: 2; }
.poker-table__seat--upper-right { grid-column: 3; grid-row: 1; }
.poker-table__seat--upper-left { grid-column: 1; grid-row: 1; }
.poker-table__status { grid-column: 2; grid-row: 2; }
.poker-table__human-anchor { grid-column: 2; grid-row: 3; align-self: end; justify-self: center; width: 100%; display: grid; justify-items: center; }
</style>
