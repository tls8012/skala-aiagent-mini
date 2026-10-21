import { actionLabel } from './publicEvents.js';

const colors = ['#ee7976', '#6b9edb', '#a68bce'];
/** 서버가 허용한 카드만 사용한다. 라운드 시작 순서로 좌석을 다시 정렬하지 않는다. */
export function toTableView(gameState, publicEvents = []) {
  const game = gameState || {};
  const players = game.players || [];
  const ai = players.filter(player => player.kind === 'ai');
  const currentEvents = publicEvents.filter(event => event.round_id === game.round_id && event.event_id <= game.event_id);
  const latestSpeech = new Map(), latestBet = new Map();
  for (const event of currentEvents) {
    if (event.type === 'Speech') latestSpeech.set(event.player_id, event);
    if (event.type === 'BetPlaced') latestBet.set(event.player_id, event);
  }
  function seat(player, index, human = false) {
    const folded = game.folded?.includes(player.id);
    const visible = game.visible_cards?.[player.id];
    const inRound = game.round_id > 0 && (!player.eliminated || visible || folded);
    // 자기 카드는 공개 전에는 항상 비공개. AI-only 관전도 공개 전에는 앞면을 받지 않는다.
    const playerPerspective = game.perspective == null ? Boolean(game.observation) : game.perspective === 'player';
    const maySeeFace = game.phase === 'revealed' || playerPerspective && !human;
    const card = !inRound ? { visibility: 'absent' } : folded ? { visibility: 'back' }
      : maySeeFace && visible ? { visibility: 'face', rank: visible.rank, suit: visible.suit } : { visibility: 'back' };
    const speech = latestSpeech.get(player.id);
    return { key: human ? 'human-anchor' : player.id, playerId: player.id, label: human ? '나' : player.id,
      role: human ? 'human' : 'ai', participation: player.eliminated ? 'spectating' : 'playing',
      position: human ? 'bottom' : ai.length === 2 ? ['upper-right', 'upper-left'][index] : ['right', 'top', 'left'][index],
      color: human ? '#111114' : colors[index % colors.length],
      gazeAngle: human ? -90 : ai.length === 2 ? [135, 45][index] : [180, 90, 0][index],
      chips: player.chips, state: player.failed ? 'failed' : player.eliminated ? 'eliminated' : folded ? 'folded' : 'active',
      isBettingTurn: game.phase === 'dialogue' && game.betting_turn === player.id,
      card, speech: speech ? { eventId: speech.event_id, roundId: speech.round_id, playerId: player.id,
        channel: speech.channel, text: speech.text } : null,
      lastActionLabel: latestBet.has(player.id) ? actionLabel(latestBet.get(player.id)) : null };
  }
  const human = players.find(player => player.id === game.human_id);
  return { aiSeats: ai.map((player, index) => seat(player, index)),
    humanSeat: human ? seat(human, 0, true) : { key: 'human-anchor', playerId: null, label: '나', role: 'human',
      participation: 'spectating', state: 'spectating', color: '#111114', gazeAngle: -90, chips: null,
      card: { visibility: 'absent' }, speech: null, isBettingTurn: false },
    summary: { roundId: game.round_id || null, phase: game.phase || 'lobby', pot: game.pot ?? null,
      currentBet: game.current_bet ?? null, bettingTurn: game.betting_turn && game.betting_turn === game.human_id ? 'human' : game.betting_turn ?? null } };
}
