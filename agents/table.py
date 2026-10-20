"""테이블 조율. AI 그래프·HTTP 없이 명령/관찰 계약만 제공한다."""
from dataclasses import asdict
import json
from pathlib import Path
import random
import threading
from uuid import uuid4

from .cards import Card, Deck
from .config import GameConfig
from .models import (AgentDecision, Bet, CommandResult, Observation, Player,
                     PlayerCommand, safe_id)
from .round import Round


class Table:
    def __init__(self, ai_ids: list[str], human_id: str | None = None,
                 config: GameConfig | None = None, game_id: str | None = None):
        self.config = config or GameConfig()
        if len(ai_ids) < 2:
            raise ValueError('at least two AI players are required')
        ids = ai_ids + ([human_id] if human_id else [])
        if len(ids) > 52 or len(set(ids)) != len(ids):
            raise ValueError('seats must be unique and no more than 52')
        self.game_id = safe_id(game_id or uuid4().hex)
        for pid in ids:
            safe_id(pid)
        self.players = {pid: Player(pid, 'human' if pid == human_id else 'ai',
                                    self.config.initial_chips) for pid in ids}
        self.seats = ids
        self.round: Round | None = None
        self.carry_pot = 0
        self.event_id = 0
        self.events: list[dict] = []
        self.game_winner: str | None = None
        self.finished = False
        self._rng = random.Random(self.config.seed)
        self._lock = threading.RLock()
        self._decisions: dict = {}
        self._requests: dict[tuple[str, str], tuple[str, CommandResult]] = {}
        self.archive_dir = self.config.data_dir / 'games' / self.game_id
        if self.archive_dir.exists() and any(self.archive_dir.iterdir()):
            raise ValueError('game_id already has records; use a new ID (resume is not implemented)')

    def _emit(self, kind: str, **data) -> dict:
        self.event_id += 1
        event = {'event_id': self.event_id, 'round_id': self.round.number,
                 'type': kind, **data}
        self.events.append(event)
        return event

    def start_round(self, cards: dict[str, Card] | None = None) -> Round:
        """cards는 테스트용 주입. 일반 실행은 설정 시드로 셔플한다."""
        with self._lock:
            if self.finished or self.round and self.round.phase != 'revealed':
                raise ValueError('cannot start a round now')
            # 이전 저장 실패가 있다면 새 라운드 전에 다시 시도한다.
            if self.round:
                self._archive()
                self.seats = self.seats[1:] + self.seats[:1]
            active = [pid for pid in self.seats if not self.players[pid].eliminated]
            if len(active) < 2:
                raise ValueError('not enough surviving players')
            if cards is None:
                cards = dict(zip(active, Deck(self._rng).deal(len(active))))
            if set(cards) != set(active) or len(set(cards.values())) != len(active):
                raise ValueError('one unique card is required per active player')
            number = self.round.number + 1 if self.round else 1
            self.round = Round(number, active, self.players, dict(cards), self.carry_pot, self.config)
            self.events = []
            self._requests.clear()
            self._decisions.clear()
            self._emit('RoundStarted', order=active, pot=self.round.pot)
            for pid in active:
                if self.players[pid].failed:
                    self._emit('AgentFailed', player_id=pid, message='나 죽었음')
            self._advance_failed()
            return self.round

    def _result(self, status: str, reason: str = '', rejudge: bool = False) -> CommandResult:
        return CommandResult(status=status, reason=reason, event_id=self.event_id, rejudge=rejudge)

    def submit(self, player_id: str, command: PlayerCommand) -> CommandResult:
        """호출자는 신뢰할 수 있는 참가자 ID를 지정한다. 같은 요청은 한 번만 적용."""
        with self._lock:
            if not isinstance(command, PlayerCommand):
                command = PlayerCommand.model_validate(command)
            key = (player_id, command.request_id)
            fingerprint = command.model_dump_json()
            if key in self._requests:
                original, result = self._requests[key]
                return result.model_copy() if original == fingerprint else self._result(
                    'rejected', 'request ID reused with a different payload')
            result = self._submit(player_id, command)
            self._requests[key] = (fingerprint, result.model_copy())
            return result

    def _submit(self, pid: str, cmd: PlayerCommand) -> CommandResult:
        r = self.round
        if r is None or r.phase != 'dialogue' or cmd.round_id != r.number:
            return self._result('rejected', 'round is not open')
        if pid not in r.order or pid in r.betting.folded or self.players[pid].failed:
            return self._result('rejected', 'player cannot act')
        if cmd.basis_event > self.event_id:
            return self._result('rejected', 'future event ID')
        c = r.conversation
        if cmd.kind == 'speak':
            reason = self._speech_error(pid, cmd)
            if reason:
                return self._result('rejected', reason)
            if c.last_speaker == pid:
                if pid in c.deferred:
                    return self._result('rejected', 'already has a deferred speech')
                c.deferred[pid] = cmd
                return self._result('deferred', 'consecutive speech; revalidate after another speaker')
            self._publish_speech(pid, cmd)
        elif cmd.kind == 'silent':
            if cmd.basis_event != self.event_id:
                return self._result('rejected', 'stale silence decision', True)
            if c.game_counts[pid] < self.config.min_utterances and not c.exhausted(pid):
                return self._result('rejected', 'minimum game speech not met')
            if pid in c.silent:
                return self._result('accepted', 'already silent')
            c.silent.add(pid)
            self._emit('PlayerSilent', player_id=pid)
        else:
            if cmd.basis_event != self.event_id:
                return self._result('rejected', 'stale betting decision', True)
            if not r.betting_open:
                return self._result('rejected', 'every active player must speak before betting')
            try:
                increment = r.betting.act(pid, cmd.bet)
            except ValueError as error:
                return self._result('rejected', str(error))
            c.silent.clear()
            c.deferred.pop(pid, None)  # 베팅 이후 본인의 이전 발언은 자동 공개하지 않는다.
            self._emit('BetPlaced', player_id=pid, action=cmd.bet.action,
                       amount=increment, raise_size=cmd.bet.raise_size, contributions=dict(r.betting.contributions),
                       current_bet=r.betting.current_bet, pot=r.pot)
        self._advance_failed()
        return self._result('accepted')

    def _speech_error(self, pid: str, cmd: PlayerCommand) -> str:
        c = self.round.conversation
        if len(cmd.text) > self.config.max_message_chars:
            return 'message too long'
        if not c.speech_allowed(pid, cmd.channel):
            return 'speech limit exhausted'
        if cmd.target and (cmd.target not in self.round.order or cmd.target == pid):
            return 'invalid target'
        if cmd.channel == 'chat':
            if self.players[pid].kind == 'human':
                needed = max(0, self.config.min_utterances - c.game_counts[pid])
                if self.config.max_utterances - c.counts[pid] <= needed:
                    return 'reserve remaining speech slots for minimum game speech'
                if not cmd.target or self.players[cmd.target].kind != 'ai' or cmd.reply_to:
                    return 'human chat must address an AI'
            elif not cmd.reply_to or c.chat_replies.get(cmd.reply_to) != pid:
                return 'AI chat requires an unanswered human request'
        elif cmd.reply_to:
            return 'game speech cannot reply to a chat request'
        return ''

    def _publish_speech(self, pid: str, cmd: PlayerCommand):
        event = self._emit('Speech', player_id=pid, channel=cmd.channel,
                           text=cmd.text, target=cmd.target, request_id=cmd.request_id)
        self.round.conversation.add(pid, cmd, event['event_id'])
        if cmd.channel == 'chat' and self.players[pid].kind == 'ai':
            del self.round.conversation.chat_replies[cmd.reply_to]

    def release_deferred(self, pid: str, basis_event: int) -> CommandResult:
        """자동 노출하지 않는다. 호출자가 최신 상황에서 재확인한 뒤 명시적으로 해제."""
        with self._lock:
            r = self.round
            if not r or r.phase != 'dialogue' or basis_event != self.event_id:
                return self._result('rejected', 'round closed or revalidation required', True)
            cmd = r.conversation.deferred.get(pid)
            if cmd is None:
                return self._result('rejected', 'no deferred speech')
            if pid in r.betting.folded or self.players[pid].failed:
                r.conversation.deferred.pop(pid)
                return self._result('rejected', 'player cannot act')
            reason = self._speech_error(pid, cmd)
            if reason:
                r.conversation.deferred.pop(pid)
                return self._result('rejected', reason)
            if r.conversation.last_speaker == pid:
                return self._result('deferred', 'another speaker is required')
            r.conversation.deferred.pop(pid)
            self._publish_speech(pid, cmd)
            self._advance_failed()
            return self._result('accepted')

    def discard_deferred(self, pid: str):
        """새 그래프 판단으로 교체할 때 아직 공개하지 않은 발언을 버린다."""
        with self._lock:
            if self.round:
                self.round.conversation.deferred.pop(pid, None)

    def fail_agent(self, pid: str):
        with self._lock:
            if pid not in self.players or self.players[pid].kind != 'ai':
                raise ValueError('not an AI player')
            if self.players[pid].failed:
                return
            self.players[pid].failed = True
            if self.round and self.round.phase == 'dialogue' and pid in self.round.order:
                self.round.conversation.deferred.pop(pid, None)
                self._emit('AgentFailed', player_id=pid, message='나 죽었음')
                self._advance_failed()

    def _advance_failed(self):
        r = self.round
        while r.phase == 'dialogue' and r.betting_open:
            if r.betting.complete:
                result = r.settle()
                self.carry_pot = result['carry_pot']
                survivors = [p for p in self.players.values() if not p.eliminated]
                winners = [p for p in survivors if p.chips >= self.config.target_chips]
                self.game_winner = max(winners, key=lambda p: p.chips).id if winners else (
                    survivors[0].id if len(survivors) == 1 else None)
                self.finished = self.game_winner is not None or not survivors
                self._emit('RoundEnded', **result, game_winner=self.game_winner,
                           finished=self.finished)
                self._archive()
                break
            pid = r.betting.turn
            if not self.players[pid].failed:
                break
            action = 'check' if r.betting.to_call(pid) == 0 else 'fold'
            r.betting.act(pid, Bet(action=action))
            r.conversation.silent.clear()
            self._emit('BetPlaced', player_id=pid, action=action, amount=0,
                       automatic=True, pot=r.pot, current_bet=r.betting.current_bet,
                       contributions=dict(r.betting.contributions))

    def observe(self, pid: str) -> Observation:
        with self._lock:
            r = self.round
            if not r or pid not in self.players:
                raise ValueError('unknown player or no round')
            c = r.conversation
            active = pid in r.betting.alive and not self.players[pid].failed
            visible = (r.result['revealed_cards'] if r.result else {
                other: card.to_dict() for other, card in r.cards.items()
                if other != pid and other not in r.betting.folded})
            return Observation(game_id=self.game_id, round_id=r.number, player_id=pid,
                event_id=self.event_id, phase=r.phase,
                players=[asdict(player) for player in self.players.values()],
                visible_cards=visible, pot=r.pot, current_bet=r.betting.current_bet,
                contributions=dict(r.betting.contributions), betting_turn=r.betting.turn,
                betting_open=r.betting_open and r.phase == 'dialogue',
                allowed_bets=r.betting.allowed(pid) if active and r.betting_open else [],
                raise_options=r.betting.raise_options(pid) if active and r.betting_open else {},
                can_speak=active and r.phase == 'dialogue' and not c.exhausted(pid)
                          and c.last_speaker != pid,
                required_game_utterances=max(0, self.config.min_utterances-c.game_counts.get(pid, 0)),
                last_speaker=c.last_speaker,
                remaining_utterances=max(0, self.config.max_utterances - c.counts.get(pid, 0)),
                dialogue_quiet=c.quiet(r.betting.alive), messages=c.context(),
                failed_agents=[p.id for p in self.players.values() if p.failed], result=r.result).model_copy(deep=True)

    def _archive(self):
        r = self.round
        if not r or r.phase != 'revealed':
            return
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        path = self.archive_dir / f'round_{r.number:06d}.json'
        if path.exists():
            return
        payload = {'game_id': self.game_id, 'round_id': r.number, 'source': 'played',
                   'events': self.events, 'result': r.result,
                   'players': [asdict(player) for player in self.players.values()]}
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)

    def apply_decision(self, pid: str, decision: AgentDecision, request_id: str) -> list[CommandResult]:
        """사용자 그래프의 출력을 연결. request_id는 실행 코드가 생성한 고유 실행 ID."""
        with self._lock:
            if self.players[pid].kind != 'ai':
                raise ValueError('only AI decisions are accepted here')
            r = self.round
            if r is None:
                raise ValueError('no round')
            if decision.round_id != r.number or r.phase != 'dialogue':
                return [self._result('rejected', 'round is not open')]
            key = (pid, request_id)
            fingerprint = decision.model_dump_json()
            if key in self._decisions:
                original, results = self._decisions[key]
                return [result.model_copy() for result in results] if original == fingerprint else [
                    self._result('rejected', 'decision ID reused with a different payload')]
            initial_event = self.event_id
            results = []
            if decision.speech != 'unavailable':
                results.append(self.submit(pid, PlayerCommand(
                    request_id=request_id + ':speech', round_id=r.number,
                    basis_event=decision.basis_event,
                    kind='speak' if decision.speech == 'speak' else 'silent',
                    text=decision.text, channel=decision.channel,
                    target=decision.target, reply_to=decision.reply_to)))
            if decision.bet:
                # 같은 결정의 발언만 사이에 추가됐다면 자체 발언으로 베팅을 무효화하지 않는다.
                basis = self.event_id if decision.basis_event == initial_event else decision.basis_event
                results.append(self.submit(pid, PlayerCommand(
                    request_id=request_id + ':bet', round_id=r.number,
                    basis_event=basis, kind='bet', bet=decision.bet)))
            self._decisions[key] = (fingerprint, [result.model_copy() for result in results])
            return results

    def events_since(self, event_id: int) -> list[dict]:
        """현재 라운드의 확정된 공개 사건. UI/그래프는 원본을 수정하지 못한다."""
        import copy
        with self._lock:
            return copy.deepcopy([event for event in self.events if event['event_id'] > event_id])

    async def call_player_tool(self, mcp, pid: str, name: str, arguments: dict | None = None):
        """신뢰된 실행 코드에서만 호출. 모델이 전달한 출처/경로는 허용하지 않는다."""
        if pid not in self.players or self.players[pid].kind != 'ai' or self.round is None:
            raise ValueError('not a seated AI or no round')
        if name not in {'read_memo', 'read_memories', 'write_memo', 'search_experiences', 'memory_status'}:
            raise ValueError('not a player tool')
        args = dict(arguments or {})
        if {'game_id', 'agent_id', 'round_id'} & args.keys():
            raise ValueError('caller identity is supplied by Table, not the model')
        return await mcp.call_tool(name, {**args, 'game_id': self.game_id,
            'agent_id': pid, 'round_id': self.round.number}, server='poker')

    async def publish_memories(self, mcp) -> list[dict]:
        """종료 기록을 MCP에 전달. 성공 표식이 없으면 재호출 가능하며 도구도 멱등 처리."""
        results = []
        for path in sorted(self.archive_dir.glob('round_*.json')):
            marker = path.with_suffix('.sent')
            if marker.exists():
                continue
            result = await mcp.call_tool('store_experience',
                {'record': json.loads(path.read_text(encoding='utf-8'))}, server='poker')
            if result.get('stored'):
                marker.touch()
            results.append(result)
        return results
