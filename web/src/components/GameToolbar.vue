<script setup>
import { computed } from 'vue';

const props = defineProps({
  sessionSummary: { type: Object, default: () => ({}) },
  connection: { type: Object, default: () => ({}) },
  readOnly: { type: Boolean, default: false },
  canStartNextRound: { type: Boolean, default: false },
});
const emit = defineEmits(['start-round', 'close-game', 'return-live']);
const statusLabel = computed(() => ({
  created: '시작 대기', running: '라운드 진행 중', summarizing: '라운드 정리 중',
  ready: '다음 라운드 대기', finished: '게임 종료', closed: '테이블 닫힘', failed: '게임 오류',
})[props.sessionSummary.status] || '테이블을 만들어 주세요');
const connectionLabel = computed(() => ({
  idle: '연결 대기', connecting: '연결 중', connected: '연결됨',
  reconnecting: '다시 연결 중', disconnected: '연결 끊김', error: '연결 오류',
})[props.connection.status] || '연결 대기');
const canStart = computed(() => Boolean(props.sessionSummary.gameId) && props.canStartNextRound
  && !props.readOnly && !props.sessionSummary.pending
  && ['created', 'ready'].includes(props.sessionSummary.status));
const canClose = computed(() => Boolean(props.sessionSummary.gameId) && !props.readOnly
  && !props.sessionSummary.pending && props.sessionSummary.status !== 'closed');
const startLabel = computed(() => props.sessionSummary.roundId > 0 ? '다음 라운드' : '첫 라운드 시작');

function startRound() {
  if (canStart.value) emit('start-round');
}
function closeGame() {
  if (canClose.value) emit('close-game');
}
</script>

<template>
  <header class="game-toolbar" :aria-busy="Boolean(sessionSummary.pending)">
    <div class="game-toolbar__main">
      <div class="game-toolbar__identity">
        <span class="game-toolbar__eyebrow">A LITTLE POKER TABLE</span>
        <h1>인디언 포커</h1>
      </div>
      <div class="game-toolbar__status" role="status">
        <span v-if="sessionSummary.roundId > 0" class="game-toolbar__round">{{ readOnly ? '현재 ' : '' }}ROUND {{ sessionSummary.roundId }}</span>
        <span>{{ statusLabel }}</span>
        <span v-if="sessionSummary.participation" class="game-toolbar__participation">{{ sessionSummary.participation === 'playing' ? '플레이 중' : '관전' }}</span>
        <span v-if="readOnly" class="game-toolbar__replay">기록 보는 중</span>
        <strong v-if="sessionSummary.gameWinner">{{ sessionSummary.gameWinner }} 우승</strong>
      </div>
      <div class="game-toolbar__actions">
        <span class="game-toolbar__connection" :class="{ 'game-toolbar__connection--online': connection.status === 'connected' }" role="status">
          <i aria-hidden="true" />{{ connectionLabel }}
        </span>
        <button v-if="readOnly" type="button" @click="emit('return-live')">현재로 돌아가기</button>
        <button class="game-toolbar__start" type="button" :disabled="!canStart" @click="startRound">{{ startLabel }}</button>
        <button type="button" :disabled="!canClose" @click="closeGame">테이블 종료</button>
      </div>
    </div>
    <p v-if="connection.error || sessionSummary.error" class="game-toolbar__error" role="alert">{{ connection.error || sessionSummary.error }}</p>
    <slot />
  </header>
</template>

<style scoped>
.game-toolbar { display: grid; gap: 18px; padding: 22px 26px; border: 1px solid #e0e3e9; border-radius: 20px; background: #ffffffb3; }
.game-toolbar__main { display: flex; align-items: center; gap: 28px; }
.game-toolbar__eyebrow { color: #9aa2af; font-size: 9px; letter-spacing: 1.7px; }
.game-toolbar h1 { margin: 5px 0 0; color: #3c4658; font-size: 21px; font-weight: 650; }
.game-toolbar__status { display: flex; gap: 10px; align-items: center; color: #778191; font-size: 12px; }
.game-toolbar__round { padding: 5px 8px; border-radius: 7px; background: #edf0f6; color: #687897; font-size: 10px; letter-spacing: .6px; }
.game-toolbar__participation, .game-toolbar__replay { padding: 4px 8px; border-radius: 20px; background: #f1f2f6; font-size: 10px; }
.game-toolbar__replay { background: #f6eede; color: #987940; }
.game-toolbar__status strong { color: #576e4a; }
.game-toolbar__actions { display: flex; gap: 8px; align-items: center; margin-left: auto; }
.game-toolbar__connection { display: inline-flex; gap: 6px; align-items: center; margin-right: 8px; color: #9199a6; font-size: 11px; }
.game-toolbar__connection i { width: 6px; height: 6px; border-radius: 50%; background: #b5bdca; }
.game-toolbar__connection--online i { background: #83a88b; }
.game-toolbar__actions button { padding: 9px 13px; border: 1px solid #dbe0e9; border-radius: 9px; background: #fff; color: #6d788c; font: inherit; font-size: 12px; cursor: pointer; }
.game-toolbar__actions .game-toolbar__start { border-color: #c4cfe2; background: #edf1f9; color: #50688d; }
.game-toolbar__actions button:hover:enabled { border-color: #9dabc2; }
.game-toolbar__actions button:disabled { opacity: .4; cursor: default; }
.game-toolbar__actions button:focus-visible { outline: 2px solid #6878ac; outline-offset: 3px; }
.game-toolbar__error { margin: 0; color: #ae4853; font-size: 12px; }
</style>
