import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createSSRApp } from 'vue';
import { renderToString } from 'vue/server-renderer';
import { createServer } from 'vite';

// 실제 SFC를 렌더링하여 비공개 카드와 발언의 HTML 경계를 확인한다.
const server = await createServer({
  root: fileURLToPath(new URL('../', import.meta.url)),
  server: { middlewareMode: true, hmr: false, ws: false },
  appType: 'custom',
});
try {
  const load = async name => (await server.ssrLoadModule(`/src/components/${name}.vue`)).default;
  const render = (component, props) => renderToString(createSSRApp(component, props));
  const Card = await load('table/PlayingCard');
  const Seat = await load('table/PlayerSeat');
  const Bubble = await load('table/SpeechBubble');
  const Log = await load('history/EventLog');

  for (const [suit, label] of [['S', '스페이드'], ['D', '다이아'], ['H', '하트'], ['C', '클로버']]) {
    assert.match(await render(Card, { card: { visibility: 'face', rank: 14, suit } }), new RegExp(`${label} A`));
  }
  for (const [rank, label] of [[13, 'K'], [12, 'Q'], [11, 'J'], [2, '2']]) {
    assert.match(await render(Card, { card: { visibility: 'face', rank, suit: 'S' } }), new RegExp(`스페이드 ${label}`));
  }
  // 호출자가 실수로 비공개 카드에 값을 보내도 DOM/접근성 이름으로 노출하지 않는다.
  const back = await render(Card, { card: { visibility: 'back', rank: 14, suit: 'S' } });
  assert.match(back, /비공개 카드/);
  assert.doesNotMatch(back, /스페이드|♠|playing-card__corner/);
  assert.doesNotMatch(await render(Card, { card: { visibility: 'absent', rank: 14, suit: 'S' } }), /playing-card|스페이드|♠/);
  assert.match(await render(Card, { card: { visibility: 'face', rank: 99, suit: 'S' } }), /비공개 카드/);
  const folded = await render(Seat, { seat: { label: 'A', state: 'folded', card: { visibility: 'face', rank: 14, suit: 'S' } } });
  assert.match(folded, /폴드/);
  assert.doesNotMatch(folded, /스페이드|♠|playing-card__corner/);
  const spectator = await render(Seat, { seat: { label: '나', state: 'spectating', card: { visibility: 'absent' } } });
  assert.match(spectator, /관전/);
  assert.doesNotMatch(spectator, /class="playing-card[" ]/);

  const text = '<img src=x onerror=alert(1)> ' + '긴 발언 '.repeat(100);
  const bubble = await render(Bubble, { speech: { eventId: 7, text } });
  assert.match(bubble, /&lt;img/);
  assert.doesNotMatch(bubble, /<img/);
  const log = await render(Log, { entries: [{ id: 'speech-7', eventId: 7, roundId: 1, text }], replayAvailable: false });
  assert.match(log, /&lt;img/);
  assert.equal(log.includes('긴 발언 '.repeat(100)), true);
  assert.doesNotMatch(log, /이 시점 보기/);
  const Setup = await load('GameSetup');
  const Toolbar = await load('GameToolbar');
  const Human = await load('controls/HumanControls');
  const Bets = await load('controls/BettingControls');
  const disabledButton = (html, label) => {
    const match = html.match(new RegExp(`<button([^>]*)>${label}</button>`));
    assert.ok(match, `button not found: ${label}`);
    return /\sdisabled(?:[\s=>]|$)/.test(match[1]);
  };
  assert.match(await render(Setup, { busy: true }), /<fieldset[^>]* disabled/);
  assert.match(await render(Setup, { error: '<script>bad</script>' }), /&lt;script&gt;/);
  const summary = { gameId: 'test', status: 'ready', roundId: 1 };
  assert.equal(disabledButton(await render(Toolbar, { sessionSummary: summary, canStartNextRound: true }), '다음 라운드'), false);
  for (const overrides of [{ readOnly: true }, { sessionSummary: { ...summary, pending: true } },
    { sessionSummary: { ...summary, status: 'running' } }, { sessionSummary: { ...summary, status: 'finished' } }]) {
    const toolbar = await render(Toolbar, { sessionSummary: summary, canStartNextRound: true, ...overrides });
    assert.equal(disabledButton(toolbar, '다음 라운드'), true);
  }
  const commands = { canSpeak: true, canDeclareSilent: true, allowedBets: ['check', 'raise'], raiseOptions: { small: 1 } };
  // 폴드 상태는 베팅 선택지만 비운다. 발언 권한을 함께 끄면 안 된다.
  const foldedInput = await render(Human, { participation: 'playing', remainingUtterances: 4, commands: { ...commands, allowedBets: [], raiseOptions: {} } });
  assert.doesNotMatch(foldedInput, /<textarea[^>]* disabled/);
  assert.equal(disabledButton(foldedInput, '발언 없음'), false);
  assert.equal(disabledButton(foldedInput, '체크'), true);
  // 발언 소진 후에도 베팅은 별도다.
  const exhaustedInput = await render(Human, { participation: 'playing', remainingUtterances: 0, commands });
  assert.match(exhaustedInput, /<textarea[^>]* disabled/);
  assert.equal(disabledButton(exhaustedInput, '체크'), false);
  for (const overrides of [{ participation: 'spectating' }, { readOnly: true }, { commands: { ...commands, pending: true } }]) {
    const input = await render(Human, { participation: 'playing', remainingUtterances: 4, commands, ...overrides });
    assert.match(input, /<textarea[^>]* disabled/);
    assert.equal(disabledButton(input, '발언 없음'), true);
    assert.equal(disabledButton(input, '체크'), true);
  }
  const deferredInput = await render(Human, { participation: 'playing', remainingUtterances: 4,
    commands: { ...commands, canRelease: true }, deferredSpeech: { text: '<img>보류한 원문' } });
  assert.match(deferredInput, /&lt;img&gt;보류한 원문/);
  assert.match(deferredInput, /<textarea[^>]* disabled/);
  assert.equal(disabledButton(deferredInput, '이 발언 공개하기'), false);
  const bets = await render(Bets, { allowedBets: ['raise'], raiseOptions: { small: 1 } });
  assert.match(bets, /작은 레이즈 <small[^>]*>\+1<\/small>/);
  assert.match(bets, /<button[^>]* disabled[^>]*>최대 레이즈/);
  assert.doesNotMatch(bets, /<button[^>]* disabled[^>]*>작은 레이즈/);
  const { useTablePreview } = await server.ssrLoadModule('/src/preview/useTablePreview.js');
  const preview = useTablePreview();
  preview.applySettings({ aiIds: ['A', 'B'], humanPlays: false });
  assert.equal(preview.view.value.aiSeats.length, 2);
  assert.equal(preview.view.value.humanSeat.card.visibility, 'absent');
  assert.equal(preview.view.value.humanSeat.chips, null);
  assert.equal(preview.view.value.aiSeats.every(seat => seat.card.visibility === 'back' && !('rank' in seat.card)), true);
  assert.equal(preview.entries.value.some(entry => entry.playerId === 'human' || entry.playerId === 'C'), false);
  console.log('Rendering checks passed: card privacy, speech/log, setup/toolbar, independent speech/betting permissions, deferred speech, spectator preview.');
} finally {
  await server.close();
}
