"""AI 테이블 한 라운드 실행기. 그래프와 게임 규칙을 연결하고 결과부터 적용한다.

MVP 실행기는 AI-only이다. 인간 입력/대기와 HTTP 연결은 나중에 추가한다.
전원 결과를 gather한 뒤 적용하지 않으며, 시간으로 침묵을 판정하지 않는다.
"""
import asyncio
from collections.abc import Callable, Mapping
from uuid import uuid4

from .models import AgentDecision


class RoundRunner:
    def __init__(self, table, graphs: Mapping[str, object], on_event: Callable | None = None):
        self.table = table
        self.graphs = dict(graphs)
        self.on_event = on_event
        self.decisions = 0
        self.errors: dict[str, str] = {}
        self.results: list[dict] = []
        self._cursor = 0
        self._turn_signal = None
        self._pending: dict[str, tuple[dict, bool]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._completed: asyncio.Queue = asyncio.Queue()

    def _active(self) -> list[str]:
        return [pid for pid in self.table.round.betting.alive
                if not self.table.players[pid].failed]

    def _enqueue(self, pid: str, event: dict, force_bet: bool = False):
        if pid in self._active():
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
            elif kind == 'Speech' and event['channel'] == 'game':
                for pid in self._active():
                    if pid != event['player_id']:
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
        for pid, (event, force_bet) in list(self._pending.items()):
            if pid in self._tasks:
                continue
            del self._pending[pid]
            if pid not in self._active():
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
            task.add_done_callback(lambda completed, actor=pid: self._completed.put_nowait((actor, completed)))

    async def _cancel_tasks(self):
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._pending.clear()

    async def _summarize_round(self):
        # 게임 중 판단을 먼저 정리하여 같은 참가자의 메모 쓰기가 회고와 겹치지 않게 한다.
        await self._cancel_tasks()
        event = next(event for event in reversed(self.table.events) if event['type'] == 'RoundEnded')
        participants = [pid for pid in self.table.round.order if not self.table.players[pid].failed]
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

    async def run_round(self, *, mcp=None, publish_memory: bool = False) -> dict:
        """진행 중 라운드를 이어가거나 새 라운드를 시작하고 공개 결과를 반환한다."""
        if any(player.kind == 'human' and not player.eliminated for player in self.table.players.values()):
            raise ValueError('this demo runner is AI-only; human UI input scheduling is not connected yet')
        missing = {p.id for p in self.table.players.values()
                   if not p.eliminated and not p.failed} - self.graphs.keys()
        if missing:
            raise ValueError(f'missing participant graphs: {sorted(missing)}')
        if publish_memory and mcp is None:
            raise ValueError('publish_memory requires a connected MCP client')
        self._pending.clear()
        self._cursor = 0
        self._turn_signal = None
        self._completed = asyncio.Queue()
        self.decisions = 0
        self.errors.clear()
        self.results.clear()
        try:
            if self.table.round is None or self.table.round.phase == 'revealed':
                self.table.start_round()
            self._dispatch()
            while self.table.round.phase == 'dialogue':
                self._launch()
                if not self._tasks:
                    r = self.table.round
                    if not r.betting_open:
                        raise RuntimeError('no runnable participant before minimum speech; inspect graph policy')
                    # 실행 중 작업과 추가 발언 제안이 모두 없으면 현재 차례의 행동을 요구한다.
                    self._enqueue(r.betting.turn, {'type': 'BetTurn', 'event_id': self.table.event_id}, True)
                    self._launch()
                pid, task = await self._completed.get()
                self._tasks.pop(pid, None)
                if pid not in self._active():
                    # folded while thinking: consume the exception but do not publish its result.
                    if not task.cancelled():
                        task.exception()
                    continue
                try:
                    decision = AgentDecision.model_validate(task.result()['decision'])
                    results = self.table.apply_decision(pid, decision, uuid4().hex)
                    self.results.append({'player_id': pid, 'basis_event': decision.basis_event,
                                         'results': [result.model_dump() for result in results]})
                    if any(result.rejudge for result in results):
                        self._enqueue(pid, {'type': 'Rejudge', 'event_id': self.table.event_id})
                except Exception as error:
                    self.errors[pid] = f'{type(error).__name__}: {error}'
                    self.table.fail_agent(pid)
                self._dispatch()
            await self._summarize_round()
            if publish_memory:
                await self.table.publish_memories(mcp)
            return dict(self.table.round.result)
        finally:
            await self._cancel_tasks()
