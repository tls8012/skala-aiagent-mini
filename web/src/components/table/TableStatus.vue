<script setup>
import { computed } from 'vue';
const props = defineProps({ summary: { type: Object, default: () => ({}) } });
const phaseLabel = computed(() => ({ deal: '배분', dialogue: '대화', revealed: '공개', finished: '종료' })[props.summary.phase] || '대기');
</script>

<template>
  <div class="table-status" aria-label="테이블 현황">
    <span class="table-status__phase">{{ summary.roundId == null ? '라운드 대기' : `ROUND ${summary.roundId}` }} · {{ phaseLabel }}</span>
    <div class="table-status__pot"><span>판돈</span><strong>{{ summary.pot ?? '—' }}</strong></div>
    <span class="table-status__bet">현재 베팅 {{ summary.currentBet ?? '—' }} · {{ summary.bettingTurn ? `${summary.bettingTurn === 'human' ? '나' : summary.bettingTurn}의 베팅 차례` : '베팅 대기' }}</span>
  </div>
</template>

<style scoped>
.table-status { justify-self: center; align-self: center; text-align: center; white-space: nowrap; }
.table-status__phase { color: #9299a5; font-size: 10px; letter-spacing: 1px; }
.table-status__pot { display: flex; justify-content: center; align-items: baseline; gap: 9px; margin: 7px 0; }
.table-status__pot > span { color: #7f8796; font-size: 12px; }
.table-status__pot strong { color: #414b61; font-size: 34px; line-height: 1; font-weight: 650; }
.table-status__bet { color: #8b93a2; font-size: 10px; }
</style>
