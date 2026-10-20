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
  console.log('Rendering checks passed: card visibility, folded/spectator seats, escaped speech, full log, replay unavailable.');
} finally {
  await server.close();
}
