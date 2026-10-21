"""HTTP 밖의 게임 조립·실행 수명·공개 사건 전달. 게임 규칙은 Table에 위임한다."""
import asyncio
from collections import deque
from copy import deepcopy
from dataclasses import asdict
import logging
import secrets

from .config import GameConfig
from agent.llm_controller import configure_backend
from .demo import ScriptedPokerLLM, ScriptedSpeechInterpreter
from .graph import build_graph
from .player_llm import PlayerLLM
from .poker_llm import PokerLLM
from .runner import RoundRunner
from .table import Table

logger = logging.getLogger(__name__)


class ServiceError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class EventJournal:
    """Table.event_id와 구분되는 전송 커서. 동기 callback에서 기록하고 비동기로 구독."""
    def __init__(self, limit: int, heartbeat: float):
        self.sequence = 0
        self.closed = False
        self._records = deque(maxlen=limit)
        self._changed = asyncio.Event()
        self.heartbeat = heartbeat

    def append(self, event: dict):
        if self.closed:
            return
        self.sequence += 1
        self._records.append({**deepcopy(event), 'sequence': self.sequence})
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()

    def since(self, cursor: int) -> list[dict]:
        if cursor < 0 or cursor > self.sequence:
            raise ServiceError(409, 'event cursor is outside this game')
        if self._records and cursor < self._records[0]['sequence'] - 1:
            raise ServiceError(409, 'event cursor expired; fetch state and reconnect')
        return deepcopy([record for record in self._records if record['sequence'] > cursor])

    def close(self):
        self.closed = True
        self._changed.set()

    async def stream(self, cursor: int):
        while True:
            changed = self._changed  # read와 wait 사이에 사건이 와도 신호를 놓치지 않는다.
            try:
                records = self.since(cursor)
            except ServiceError:
                yield {'type': 'ResyncRequired', 'stream_cursor': self.sequence}
                return
            for record in records:
                cursor = record['sequence']
                yield record
            if self.closed:
                return
            if records:
                continue
            try:
                await asyncio.wait_for(changed.wait(), self.heartbeat)
            except TimeoutError:
                yield None  # SSE heartbeat. 게임의 침묵/턴 상태에는 관여하지 않는다.


class GameSession:
    def __init__(self, table: Table, backend: str, mcp=None):
        table.config = configure_backend(table.config, backend)
        self.table = table
        self.backend = backend
        self.mcp = mcp
        self.access_token = secrets.token_urlsafe(32)
        self.human_id = next((p.id for p in table.players.values() if p.kind == 'human'), None)
        self.journal = EventJournal(table.config.event_history_limit, table.config.sse_heartbeat_seconds)
        self._state = 'created'
        self._error = None
        self.memory_status = 'disabled' if mcp is None else 'idle'
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        backends = {}
        for pid, player in table.players.items():
            if player.kind == 'ai':
                backends[pid] = (PokerLLM(table, mcp, pid, PlayerLLM(table, mcp, pid))
                                 if backend != 'fake' else ScriptedPokerLLM(table, None, pid, None))
        self.runner = RoundRunner(table, {pid: build_graph(llm) for pid, llm in backends.items()},
                                  on_event=self._publish_event,
                                  interpreter=ScriptedSpeechInterpreter() if backend == 'fake' else None)

    def _publish_event(self, event: dict):
        self.journal.append(self.table.history.decorate(event))

    def history(self, round_id: int | None = None) -> dict:
        try:
            return self.table.replay_history(round_id)
        except KeyError:
            raise ServiceError(404, 'round history not found') from None

    def snapshot(self) -> dict:
        table, r = self.table, self.table.round
        running = self._task is not None and not self._task.done()
        status = ('summarizing' if self._state == 'running' and r and r.phase == 'revealed'
                  else self._state)
        observation = (table.observe(self.human_id).model_dump()
                       if self.human_id and r and self.human_id in r.order else None)
        pending = r.conversation.deferred.get(self.human_id) if r and self.human_id else None
        return {
            'game_id': table.game_id, 'status': status, 'backend': self.backend,
            'human_id': self.human_id, 'round_id': r.number if r else 0,
            'event_id': table.event_id, 'stream_cursor': self.journal.sequence,
            'phase': r.phase if r else 'lobby',
            'players': [asdict(player) for player in table.players.values()],
            'folded': sorted(r.betting.folded) if r else [],
            'pot': r.pot if r else table.carry_pot,
            'current_bet': r.betting.current_bet if r else 0,
            'betting_turn': r.betting.turn if r else None,
            'visible_cards': deepcopy(observation['visible_cards'] if observation else
                                     r.result['revealed_cards'] if r and r.result else {}),
            'result': deepcopy(r.result) if r else None,
            'game_winner': table.game_winner, 'finished': table.finished,
            'observation': observation,
            'deferred_speech': pending.model_dump() if pending else None,
            # 현재 라운드의 공개 기록만 보충한다. 개인 메모/자기 비공개 카드는 없다.
            'recent_events': [table.history.decorate(event) for event in
                              table.events_since(0)[-table.config.event_history_limit:]] if r else [],
            'is_silent': bool(r and self.human_id in r.conversation.silent),
            'can_start_next_round': self._state not in {'closed', 'failed'} and not running
                and not table.finished and (r is None or r.phase == 'revealed'),
            'memory_status': self.memory_status, 'error': self._error,
            'rules': {name: getattr(table.config, name) for name in
                      ('initial_chips', 'ante', 'target_chips', 'max_utterances', 'min_utterances',
                       'max_message_chars', 'event_history_limit')},
        }

    async def start_round(self, round_id: int) -> dict:
        async with self._lock:
            current = self.table.round.number if self.table.round else 0
            if round_id == current and current:
                return self.snapshot()  # 같은 시작 요청으로 재배분하지 않는다.
            if self._state in {'closed', 'failed'} or self.table.finished:
                raise ServiceError(409, 'game cannot start another round')
            if round_id != current + 1:
                raise ServiceError(409, 'round_id must be the next round number')
            if self._task is not None and not self._task.done():
                raise ServiceError(409, 'round is running or summarizing')
            if self.table.round and self.table.round.phase != 'revealed':
                raise ServiceError(409, 'current round has not ended')
            self._state = 'running'
            self.runner.started.clear()
            self._task = asyncio.create_task(self._run(), name=f'poker:{self.table.game_id}:{round_id}')
            ready = asyncio.create_task(self.runner.started.wait())
            try:
                await asyncio.wait({self._task, ready}, return_when=asyncio.FIRST_COMPLETED)
                if not self.runner.started.is_set():
                    raise ServiceError(500, 'round failed to start')
            finally:
                ready.cancel()
                await asyncio.gather(ready, return_exceptions=True)
            return self.snapshot()

    async def _run(self):
        try:
            await self.runner.run_round()
            if self.mcp is not None:
                self.memory_status = 'publishing'
                try:
                    await self.table.publish_memories(self.mcp)
                    self.memory_status = 'queued'
                except Exception:
                    logger.exception('Experience publishing failed for game %s', self.table.game_id)
                    self.memory_status = 'failed'
                    self.journal.append({'type': 'MemoryPublishFailed', 'round_id': self.table.round.number})
            self._state = 'finished' if self.table.finished else 'ready'
            self.journal.append({'type': 'RoundReady', 'round_id': self.table.round.number,
                                 'finished': self.table.finished})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('Round failed for game %s', self.table.game_id)
            self._state, self._error = 'failed', 'round execution failed'
            self.journal.append({'type': 'RunFailed', 'round_id': self.table.round.number if self.table.round else 0,
                                 'message': self._error})

    async def command(self, command):
        if self.human_id is None:
            raise ServiceError(409, 'this game has no human player')
        result = await self.runner.submit_human(self.human_id, command)
        return {'result': result.model_dump(), 'game': self.snapshot()}

    async def release(self, round_id: int, basis_event: int):
        if self.human_id is None:
            raise ServiceError(409, 'this game has no human player')
        result = await self.runner.release_human_speech(self.human_id, round_id, basis_event)
        return {'result': result.model_dump(), 'game': self.snapshot()}

    async def close(self):
        async with self._lock:
            if self._state == 'closed':
                return
            if self._task is not None and not self._task.done():
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
            self._state = 'closed'
            self.table.clear_replay_history()
            self.journal.append({'type': 'GameClosed'})
            self.journal.close()


class GameService:
    def __init__(self, config: GameConfig, backend: str = 'fake', mcp=None):
        config = configure_backend(config, backend)
        if backend != 'fake' and mcp is None:
            raise ValueError(f'{backend} backend requires a connected MCP client')
        self.config, self.backend, self.mcp = config, backend, mcp
        self._games: dict[str, GameSession] = {}

    @property
    def game_count(self):
        return len(self._games)

    def create(self, ai_ids: list[str], human_id: str | None):
        session = GameSession(Table(ai_ids, human_id, self.config), self.backend, self.mcp)
        self._games[session.table.game_id] = session
        return session

    def get(self, game_id: str, token: str) -> GameSession:
        session = self._games.get(game_id)
        if session is None:
            raise ServiceError(404, 'game not found')
        if not secrets.compare_digest(session.access_token.encode(), token.encode()):
            raise ServiceError(403, 'invalid game access token')
        return session

    async def remove(self, game_id: str, token: str):
        session = self.get(game_id, token)
        await session.close()
        self._games.pop(game_id, None)

    async def close(self):
        await asyncio.gather(*(session.close() for session in list(self._games.values())))
        self._games.clear()
