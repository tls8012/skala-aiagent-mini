"""인간 명령과 AI 완료 결과를 한 큐에서 처리하는 테이블 실행기."""
import asyncio
from collections import deque
from collections.abc import Callable, Mapping
from uuid import uuid4

from .models import AgentDecision, CommandResult, PlayerCommand
from .speech_interpreter import SpeechInterpreter


class RoundRunner:
    def __init__(self, table, graphs: Mapping[str, object], on_event: Callable | None = None,
                 interpreter=None):
        self.table = table
        self.graphs = dict(graphs)
        self.on_event = on_event
        self.interpreter = interpreter if interpreter is not None else SpeechInterpreter(table.config)
        self.started = asyncio.Event()
        self.running = False
        self.interpretation_errors: dict[str, str] = {}
        self._mailboxes: dict[str, deque] = {}
        self._human_cache: dict[tuple, tuple[str, CommandResult]] = {}
        self._current_input = None
        self.decisions = 0
        self.errors: dict[str, str] = {}
        self.results: list[dict] = []
        self._cursor = 0
        self._turn_signal = None
        self._pending: dict[str, tuple[dict, bool]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._task_events: dict[str, dict] = {}
        self._inbox: asyncio.Queue = asyncio.Queue()

    def _active(self) -> list[str]:
        """그래프를 실행할 수 있는 AI만 반환한다. 인간의 생존 여부는 Table이 관리."""
        return [pid for pid in self.table.round.betting.alive
                if self.table.players[pid].kind == 'ai' and not self.table.players[pid].failed]

    @staticmethod
    def _addressed(event: dict, pid: str) -> bool:
        if event.get('type') != 'Speech':
            return False
        interpretation = event.get('interpretation')
        if interpretation:
            return any(intent['kind'] != 'game' and pid in intent['targets']
                       for intent in interpretation['intents'])
        return event.get('channel') == 'chat' and event.get('target') == pid

    def _enqueue(self, pid: str, event: dict, force_bet: bool = False):
        if pid in self._active():
            sender = self.table.players.get(event.get('player_id'))
            if self._addressed(event, pid) and sender is not None and sender.kind == 'human':
                mailbox = self._mailboxes.setdefault(pid, deque())
                if not any(item['event_id'] == event['event_id'] for item in mailbox):
                    mailbox.append(event)
            else:
                previous = self._pending.get(pid)
                self._pending[pid] = (event, force_bet or bool(previous and previous[1]))

    def _dispatch(self):
        for event in self.table.events_since(self._cursor):
            self._cursor = event['event_id']
            if self.on_event:
                self.on_event(event)
            kind = event['type']
            if kind in {'RoundStarted', 'BetPlaced', 'AgentFailed'}:
                for pid in self._active():
                    self._enqueue(pid, event)
            elif kind == 'Speech' and event['channel'] in {'game', 'mixed'}:
                for pid in self._active():
                    if pid != event['player_id']:
                        self._enqueue(pid, event)
            elif kind == 'Speech' and event['channel'] == 'chat':
                for pid in self._active():
                    if self._addressed(event, pid):
                        self._enqueue(pid, event)
            # PlayerSilent는 다른 참가자를 깨우지 않는다. 침묵 선언의 왕복 루프 방지.
        r = self.table.round
        if r.phase == 'dialogue' and r.betting_open and r.betting.turn in self._active():
            pid = r.betting.turn
            signal = (r.number, pid, r.betting.current_bet)
            if signal != self._turn_signal:
                self._turn_signal = signal
                if pid not in self._tasks and pid not in self._pending:
                    self._enqueue(pid, {'type': 'BetTurn', 'event_id': self.table.event_id},
                                  r.conversation.quiet(r.betting.alive))

    def _launch(self):
        for pid in self._active():
            if pid in self._tasks:
                continue
            mailbox = self._mailboxes.get(pid, deque())
            while mailbox and pid not in self.table.round.conversation.chat_replies.get(mailbox[0]['request_id'], set()):
                mailbox.popleft()
            needs_game = self.table.round.conversation.game_counts[pid] < self.table.config.min_utterances
            if mailbox and needs_game and mailbox[0].get('channel') == 'chat' and pid in self._pending:
                # 일반 답변만 먼저 하고 최소 게임 발언을 놓치지 않게 한다. 질문은 보존한다.
                mailbox = deque()
            if mailbox and self.table.round.conversation.last_speaker == pid:
                # 연속 답변은 다른 발언이 생길 때까지 보관한다. 베팅 판단은 별도로 가능하다.
                mailbox = deque()
            if mailbox:
                event, force_bet = mailbox.popleft(), False
            elif pid in self._pending:
                event, force_bet = self._pending.pop(pid)
            else:
                continue
            if self.decisions >= self.table.config.runner_max_decisions:
                raise RuntimeError('runner_max_decisions reached; inspect graph policy before continuing')
            observation = self.table.observe(pid)
            if pid in self.table.round.conversation.deferred and observation.last_speaker != pid:
                # 보류된 옛 문장을 자동 공개하지 않고 최신 관찰로 재판단한다.
                self.table.discard_deferred(pid)
            self.decisions += 1
            task = asyncio.create_task(self.graphs[pid].ainvoke({
                'observation': observation.model_dump(), 'event': event, 'force_bet': force_bet}))
            self._tasks[pid] = task
            self._task_events[pid] = event
            task.add_done_callback(lambda completed, actor=pid: self._inbox.put_nowait(('ai', actor, completed, None)))

    async def _cancel_tasks(self):
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._task_events.clear()
        self._pending.clear()
        self._mailboxes.clear()

    async def _summarize_round(self):
        # 게임 중 판단을 먼저 정리하여 같은 참가자의 메모 쓰기가 회고와 겹치지 않게 한다.
        await self._cancel_tasks()
        event = next(event for event in reversed(self.table.events) if event['type'] == 'RoundEnded')
        participants = [pid for pid in self.table.round.order
                        if self.table.players[pid].kind == 'ai' and not self.table.players[pid].failed]
        observations = {pid: self.table.observe(pid) for pid in participants}
        self.decisions += len(participants)
        # 공개 후 메모 회고는 동시 진행해도 게임의 발언 시점/베팅 순서에 영향을 주지 않는다.
        outputs = await asyncio.gather(*(self.graphs[pid].ainvoke({
            'observation': observations[pid].model_dump(), 'event': event, 'force_bet': False})
            for pid in participants), return_exceptions=True)
        for pid, output in zip(participants, outputs):
            if isinstance(output, BaseException):
                self.errors[pid] = f'round summary: {type(output).__name__}: {output}'
                self.table.fail_agent(pid)

    async def submit_human(self, player_id: str, command: PlayerCommand) -> CommandResult:
        """서버가 확인한 참가자 ID로 호출. 반환값은 규칙 검증이 끝난 접수 결과다."""
        command = PlayerCommand.model_validate(command).model_copy(deep=True)
        key = (command.round_id, player_id, command.request_id)
        cached = self._human_cache.get(key)
        if cached:
            original, result = cached
            return result.model_copy() if original == command.model_dump_json() else self.table._result(
                'rejected', 'request ID reused with a different payload')
        return await self._submit_input('human', player_id, command)

    async def release_human_speech(self, player_id: str, round_id: int, basis_event: int) -> CommandResult:
        """연속 발언으로 보류된 원문을 인간이 최신 상황에서 재확인한 후 호출한다."""
        return await self._submit_input('release', player_id, (round_id, basis_event))

    async def _submit_input(self, kind, pid, payload) -> CommandResult:
        if pid not in self.table.players or self.table.players[pid].kind != 'human':
            return self.table._result('rejected', 'not a human player')
        if not self.running or self.table.round is None or self.table.round.phase != 'dialogue':
            return self.table._result('rejected', 'round is not open')
        future = asyncio.get_running_loop().create_future()
        self._inbox.put_nowait((kind, pid, payload, future))
        return await asyncio.shield(future)

    async def _human_command(self, pid, command) -> CommandResult:
        key = (command.round_id, pid, command.request_id)
        fingerprint = command.model_dump_json()
        if key in self._human_cache:
            previous, result = self._human_cache[key]
            return result.model_copy() if previous == fingerprint else self.table._result(
                'rejected', 'request ID reused with a different payload')
        result = await self._prepare_human_command(pid, command)
        self._human_cache[key] = (fingerprint, result.model_copy())
        return result

    async def _prepare_human_command(self, pid, command) -> CommandResult:
        r = self.table.round
        interpretation = None
        if command.round_id != r.number or pid not in r.betting.alive or self.table.players[pid].failed:
            return self.table._result('rejected', 'round closed or player cannot act')
        if command.kind == 'speak':
            if len(command.text) > self.table.config.max_message_chars or r.conversation.exhausted(pid):
                return self.table._result('rejected', 'speech limit exceeded')
            ai_ids = [other for other in r.order if self.table.players[other].kind == 'ai']
            try:
                interpretation = await self.interpreter.interpret(command.text, ai_ids, command.target)
            except Exception as error:
                self.interpretation_errors[command.request_id] = f'{type(error).__name__}: {error}'
                return self.table._result('rejected', 'speech interpretation failed; retry with a new request ID')
        return self.table.submit(pid, command, interpretation=interpretation)

    async def run_round(self, *, mcp=None, publish_memory: bool = False) -> dict:
        """백그라운드 task로 실행. 인간 입력은 submit_human()으로 별도 제출한다."""
        if self.running:
            raise RuntimeError('round runner is already running')
        missing = {p.id for p in self.table.players.values()
                   if p.kind == 'ai' and not p.eliminated and not p.failed} - self.graphs.keys()
        if missing:
            raise ValueError(f'missing participant graphs: {sorted(missing)}')
        if publish_memory and mcp is None:
            raise ValueError('publish_memory requires a connected MCP client')
        self._pending.clear()
        self._mailboxes.clear()
        self._human_cache.clear()
        self._cursor = 0
        self._turn_signal = None
        self._inbox = asyncio.Queue()
        self.decisions = 0
        self.errors.clear()
        self.interpretation_errors.clear()
        self.results.clear()
        self.started.clear()
        self.running = True
        try:
            if self.table.round is None or self.table.round.phase == 'revealed':
                self.table.start_round()
            self._dispatch()
            self.started.set()
            while self.table.round.phase == 'dialogue':
                self._launch()
                if not self._tasks:
                    r = self.table.round
                    if r.betting_open and r.betting.turn in self._active():
                        self._enqueue(r.betting.turn, {'type': 'BetTurn', 'event_id': self.table.event_id}, True)
                        self._launch()
                    elif not r.betting_open and not any(
                            self.table.players[pid].kind == 'human' for pid in r.betting.alive):
                        raise RuntimeError('no runnable participant before minimum speech; inspect graph policy')
                item = await self._inbox.get()
                self._current_input = item
                kind, pid, payload, future = item
                if kind != 'ai':
                    if kind == 'human':
                        result = await self._human_command(pid, payload)
                    else:
                        number, basis = payload
                        result = (self.table.release_deferred(pid, basis) if number == self.table.round.number
                                  else self.table._result('rejected', 'round is not open'))
                    if not future.done():
                        future.set_result(result)
                else:
                    task = payload
                    self._tasks.pop(pid, None)
                    source_event = self._task_events.pop(pid, {})
                    if pid not in self._active():
                        if not task.cancelled():
                            task.exception()
                    else:
                        try:
                            decision = AgentDecision.model_validate(task.result()['decision'])
                            results = self.table.apply_decision(pid, decision, uuid4().hex)
                            self.results.append({'player_id': pid, 'basis_event': decision.basis_event,
                                                 'results': [result.model_dump() for result in results]})
                            if any(result.status == 'deferred' for result in results) and self._addressed(source_event, pid):
                                self._enqueue(pid, source_event)
                            if any(result.rejudge for result in results):
                                self._enqueue(pid, {'type': 'Rejudge', 'event_id': self.table.event_id})
                        except Exception as error:
                            self.errors[pid] = f'{type(error).__name__}: {error}'
                            self.table.fail_agent(pid)
                self._current_input = None
                self._dispatch()
            await self._summarize_round()
            if publish_memory:
                await self.table.publish_memories(mcp)
            return dict(self.table.round.result)
        finally:
            self.running = False
            await self._cancel_tasks()
            waiting = [self._current_input] if self._current_input else []
            while not self._inbox.empty():
                waiting.append(self._inbox.get_nowait())
            for kind, pid, payload, future in waiting:
                if future is not None and not future.done():
                    future.set_result(self.table._result('rejected', 'round runner closed'))
                elif kind == 'ai' and payload.done() and not payload.cancelled():
                    payload.exception()
            self._current_input = None
