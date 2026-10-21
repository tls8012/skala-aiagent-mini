"""실험 설정과 관찰 전용 로그. 게임 상태는 기존 엔진이 관리한다."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import time
from uuid import uuid4
import asyncio

from langchain_core.callbacks import BaseCallbackHandler

from agents.config import GameConfig
from agents.table import Table
from agent.llm_controller import GAME_BACKENDS, configure_backend, configure_model

ACTIVE_DECISION = ContextVar('experiment_decision', default=None)
CONDITIONS = {
    'control': (False, False), 'search_only': (True, False),
    'memo_only': (False, True), 'both': (True, True),
}
TRUST_CONDITIONS = ('no_information', 'trustworthy_history', 'deceptive_history')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def plain(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


@dataclass(frozen=True)
class ExperimentConfig:
    stage: int
    cases: int | None = None
    backend: str = field(default_factory=lambda: GameConfig().llm_backend)
    seed: int = 42
    output: Path = Path('experiments/results')
    model: str | None = None
    conditions: tuple[str, ...] = ()
    timeout: float = 600
    index_timeout: float = 180

    def __post_init__(self):
        if self.cases is None:
            object.__setattr__(self, 'cases', 10 if self.stage == 4 else 30)
        if self.stage not in (1, 2, 3, 4):
            raise ValueError('stage=1..4')
        maximum = 10 if self.stage == 4 else 30
        if type(self.cases) is not int or not 1 <= self.cases <= maximum:
            raise ValueError(f'stage {self.stage}: cases=1..{maximum}')
        if self.backend not in GAME_BACKENDS or self.timeout <= 0 or self.index_timeout <= 0:
            raise ValueError('invalid backend/timeout')
        allowed = (('no_information', 'relevant_history', 'unrelated_history') if self.stage == 1
                   else TRUST_CONDITIONS if self.stage == 4 else tuple(CONDITIONS))
        if any(c not in allowed for c in self.conditions) or len(set(self.conditions)) != len(self.conditions):
            raise ValueError('invalid/duplicate conditions for this stage')

    @property
    def arms(self):
        return self.conditions or (TRUST_CONDITIONS if self.stage == 4
                                   else ('no_information', 'relevant_history', 'unrelated_history') if self.stage == 1
                                   else tuple(CONDITIONS) if self.stage == 2 else ('control', 'both'))


class RunRecorder:
    def __init__(self, config: ExperimentConfig):
        self.config = config
        self.run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:8]
        self.root = config.output.resolve() / self.run_id
        self.root.mkdir(parents=True, exist_ok=False)
        self.started = time.perf_counter()
        self._lock = threading.RLock()
        self.proposals = {}
        self.active_models = set()
        self.manifest = {'run_id': self.run_id, 'status': 'running', 'config': plain(asdict(config)),
                         'started_at': self.now(), 'backend': config.backend,
                         'evidence_run': config.backend != 'fake', 'temperature': 0.0,
                         'planned_trials': config.cases * len(config.arms) * (2 if config.stage == 3 else 1),
                         'conditions': config.arms, 'model_digest': None,
                         'timing_scope': 'client wall clock; spans are inclusive and may overlap',
                         'ttft': 'unavailable: existing complete() returns a complete JSON response',
                         'source_hashes': {str(p): digest(p.read_text()) for p in [
                             *(p for folder in ('agents', 'agent', 'experiments', 'agents/prompts')
                               for p in sorted(Path(folder).glob('*.py'))),
                             *sorted(Path('agents/prompts').glob('*.txt'))]}}
        self.save_manifest()

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    def save_manifest(self):
        (self.root / 'manifest.json').write_text(json.dumps(self.manifest, ensure_ascii=False, indent=2, default=str))

    def emit(self, stream, **row):
        record = {'run_id': self.run_id, 'recorded_at': self.now(),
                  'decision_id': ACTIVE_DECISION.get(), **row}
        with self._lock, (self.root / f'{stream}.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')

    @contextmanager
    def span(self, kind, name, **fields):
        start = time.perf_counter()
        status, failure = 'ok', None
        try:
            yield
        except BaseException as error:
            status, failure = ('cancelled' if isinstance(error, asyncio.CancelledError) else 'error'), f'{type(error).__name__}: {error}'
            raise
        finally:
            self.emit('timings', kind=kind, name=name, started_offset_s=start-self.started,
                      duration_s=time.perf_counter()-start, status=status, error=failure, **fields)

    def game_config(self, trial_id):
        config = configure_backend(GameConfig(data_dir=self.root / 'data' / trial_id, seed=self.config.seed), self.config.backend)
        return configure_model(config, self.config.model)

    def finish(self, status='complete'):
        self.manifest.update(status=status, finished_at=self.now(), total_duration_s=time.perf_counter()-self.started)
        self.save_manifest()

    async def drain_models(self):
        # 기존 동기 HTTP 요청은 asyncio task 취소 후에도 스레드에서 끝날 수 있다.
        # 그 응답/시간 로그까지 수집한 뒤 다음 조건을 시작한다.
        deadline = time.perf_counter() + 310
        while self.active_models:
            if time.perf_counter() > deadline:
                raise TimeoutError('in-flight model requests did not finish')
            await asyncio.sleep(0.05)


class ObservedTable(Table):
    """동일 엔진의 행동 접수만 관찰한다. 그래프의 제안 ID와 확정 사건을 연결."""
    def __init__(self, *args, recorder, trial_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.recorder, self.trial_id = recorder, trial_id

    def apply_decision(self, pid, decision, request_id):
        ids = self.recorder.proposals.get((pid, digest(decision.model_dump())), [])
        decision_id = ids.pop(0) if ids else None
        token = ACTIVE_DECISION.set(decision_id)
        before = self.event_id
        try:
            with self.recorder.span('engine', 'apply_decision', trial_id=self.trial_id):
                results = super().apply_decision(pid, decision, request_id)
                self.recorder.emit('decisions', event='applied', trial_id=self.trial_id, player_id=pid,
                                   proposed=decision.model_dump(), results=[r.model_dump() for r in results],
                                   public_events=self.events_since(before))
                return results
        finally:
            ACTIVE_DECISION.reset(token)


class NodeTimingHandler(BaseCallbackHandler):
    """LangGraph callback의 실제 node run만 기록한다. 하위 Runnable의 중복 span은 제외."""
    run_inline = True
    raise_error = True
    NODES = {'route', 'research', 'answer', 'validate', 'remember'}

    def __init__(self, recorder, trial_id, decision_id):
        self.recorder, self.trial_id, self.decision_id = recorder, trial_id, decision_id
        self.runs = {}

    def on_chain_start(self, serialized, inputs, *, run_id, metadata=None, **kwargs):
        name = kwargs.get('name')
        if name in self.NODES and (metadata or {}).get('langgraph_node') == name:
            self.runs[run_id] = (name, time.perf_counter())

    def _end(self, run_id, error=None):
        if run_id not in self.runs:
            return
        name, start = self.runs.pop(run_id)
        self.recorder.emit('timings', kind='node', name=name, trial_id=self.trial_id,
                           decision_id=self.decision_id, started_offset_s=start-self.recorder.started,
                           duration_s=time.perf_counter()-start,
                           status='cancelled' if isinstance(error, asyncio.CancelledError) else 'error' if error else 'ok',
                           error=str(error) if error else None)

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        self._end(run_id)

    def on_chain_error(self, error, *, run_id, **kwargs):
        self._end(run_id, error)
