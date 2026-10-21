/** 발언/베팅 권한을 따로 전달한다. 폴드 여부로 발언을 끄지 않는다. */
export function toCommandState(game, { pending = false, synchronized = false, uncertain = false, error = null, speechReceipt = null } = {}) {
  const observation = game?.observation;
  const human = game?.players?.find(player => player.id === game.human_id);
  const open = Boolean(human && !human.eliminated && !human.failed && observation && game.phase === 'dialogue'
    && !['closed', 'failed'].includes(game.status));
  const disabled = !open || !synchronized || uncertain;
  const remaining = observation?.remaining_utterances || 0;
  return { contextKey: `${game?.game_id || 'lobby'}:${game?.round_id || 0}`, pending, disabled,
    disabledReason: uncertain ? '접수 여부가 불확실합니다. 같은 요청 다시 확인을 눌러 주세요.'
      : !synchronized && game ? '최신 상태를 확인하는 중이에요.'
      : !open ? '라운드가 열리면 입력할 수 있어요.' : null,
    canSpeak: open && Boolean(observation.can_speak),
    canDeclareSilent: open && !game.is_silent && (observation.required_game_utterances === 0 || remaining === 0),
    canRelease: open && Boolean(game.deferred_speech) && remaining > 0,
    isSilent: Boolean(game?.is_silent), maxMessageChars: game?.rules?.max_message_chars,
    allowedBets: open ? observation.allowed_bets : [], raiseOptions: open ? observation.raise_options : {}, error, speechReceipt };
}
