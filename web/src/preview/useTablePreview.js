// 화면 개발 전용 상태. 버튼 입력을 확인할 뿐 서버/게임/기록을 실행하거나 변경하지 않는다.
import { computed, ref } from 'vue';
import { previewView, previewEntries } from './tablePreview.js';

export function useTablePreview() {
  const options = ref({ aiIds: ['A', 'B', 'C'], humanPlays: true });
  const settingsRevision = ref(0);
  const mode = ref('turn');
  const notice = ref('설정을 적용하거나 아래 입력 버튼을 눌러 동작을 확인해 보세요.');
  const ready = computed(() => mode.value === 'ready');
  const bettingTurn = computed(() => ['turn', 'exhausted'].includes(mode.value) && options.value.humanPlays);
  const participation = computed(() => options.value.humanPlays ? 'playing' : 'spectating');
  const remaining = computed(() => !options.value.humanPlays || mode.value === 'exhausted' ? 0 : 4);
  const view = computed(() => {
    const turn = ready.value ? null : bettingTurn.value ? 'human' : 'B';
    const aiSeats = previewView.aiSeats.filter(seat => options.value.aiIds.includes(seat.playerId)).map((seat, index) => ({
      ...seat,
      position: options.value.aiIds.length === 2 ? ['upper-right', 'upper-left'][index] : seat.position,
      gazeAngle: options.value.aiIds.length === 2 ? [135, 45][index] : seat.gazeAngle,
      card: options.value.humanPlays ? seat.card : { visibility: 'back' },
      isBettingTurn: seat.playerId === turn,
    }));
    const humanSeat = options.value.humanPlays ? {
      ...previewView.humanSeat, state: mode.value === 'folded' ? 'folded' : 'active', isBettingTurn: turn === 'human',
    } : {
      key: 'human-anchor', playerId: null, label: '나', role: 'human', participation: 'spectating',
      state: 'spectating', color: '#111114', gazeAngle: -90, chips: null, card: { visibility: 'absent' }, speech: null,
    };
    return { aiSeats, humanSeat, summary: { ...previewView.summary, phase: ready.value ? 'revealed' : 'dialogue', bettingTurn: turn } };
  });
  const entries = computed(() => previewEntries.filter(entry => !entry.playerId
    || options.value.aiIds.includes(entry.playerId) || entry.playerId === 'human' && options.value.humanPlays));
  const commands = computed(() => ({
    contextKey: `ui-preview:${settingsRevision.value}:1`, disabled: ready.value,
    disabledReason: ready.value ? '라운드가 끝났어요. 다음 라운드를 기다려 주세요.' : null,
    pending: false, canSpeak: remaining.value > 0 && mode.value !== 'deferred',
    canDeclareSilent: remaining.value > 0, canRelease: mode.value === 'deferred',
    allowedBets: bettingTurn.value ? ['check', 'raise', 'fold'] : [],
    raiseOptions: bettingTurn.value ? { maximum: 12, large: 4, medium: 2, small: 1 } : {},
  }));
  const deferredSpeech = computed(() => mode.value === 'deferred' && options.value.humanPlays
    ? { text: '잠깐, B의 설명을 조금 더 듣고 결정하고 싶어.' } : null);
  const sessionSummary = computed(() => ({
    gameId: 'ui-preview', status: ready.value ? 'ready' : 'running', roundId: 1,
    participation: participation.value, pending: false,
  }));
  function applySettings(value) {
    options.value = { aiIds: [...value.aiIds], humanPlays: value.humanPlays };
    settingsRevision.value += 1;
    notice.value = `AI ${value.aiIds.length}명 · ${value.humanPlays ? '플레이' : '관전'} 설정을 미리보기에 적용했어요.`;
  }
  function recordAction(label) {
    notice.value = `${label} 입력을 확인했어요. 미리보기이므로 실제 게임 상태는 바뀌지 않아요.`;
  }
  function recordBet(value) {
    const action = { check: '체크', call: '콜', fold: '폴드', raise: '레이즈' }[value.action];
    const size = { maximum: '최대', large: '큰', medium: '중간', small: '작은' }[value.raise_size];
    recordAction(`베팅: ${size ? `${size} ` : ''}${action}`);
  }
  return { mode, notice, view, entries, commands, deferredSpeech, sessionSummary,
    participation, remaining, canStartNextRound: ready, applySettings, recordAction, recordBet };
}
