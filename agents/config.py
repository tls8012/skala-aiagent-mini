"""자주 바꾸는 게임·컨텍스트·기억 설정."""
from dataclasses import asdict, dataclass, field
from pathlib import Path
import os
import json


@dataclass(frozen=True)
class GameConfig:
    initial_chips: int = 30
    ante: int = 2
    target_chips: int = 60
    max_utterances: int = 5
    min_utterances: int = 1
    min_raise: int = 1
    context_message_limit: int = 40
    memory_round_limit: int = 20
    max_message_chars: int = 4000
    event_history_limit: int = 2000
    sse_heartbeat_seconds: float = 15.0
    round_memo_max_chars: int = 1000
    game_memo_max_chars: int = 2000
    long_memo_max_chars: int = 3000
    graph_max_attempts: int = 3
    runner_max_decisions: int = 120
    llm_backend: str = 'openai' # fake, openai, ollama
    openai_model: str = 'gpt-6-terra'
    openai_router_model: str | None = None
    openai_max_output_tokens: int = 1024
    openai_router_max_output_tokens: int = 768
    openai_reasoning_effort: str | None = 'none'
    openai_timeout: float = 120.0
    openai_usd_per_1k_tokens: float | None = None
    router_model: str = 'qwen3.5:2b'
    router_num_predict: int = 768
    router_max_attempts: int = 2
    router_timeout: float = 30.0
    ollama_model: str = 'qwen3:8b'
    ollama_think: bool | None = False
    ollama_num_predict: int = 512
    ollama_usd_per_1k_tokens: float = 0.015
    seed: int | None = None
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get(
        'POKER_DATA_DIR', str(Path(__file__).parent / 'data'))))
    embedding_url: str = 'http://127.0.0.1:11434/api/embed'
    embedding_model: str = 'embeddinggemma:latest'
    embedding_context_tokens: int = 2048
    embedding_chunk_max_bytes: int = 1536
    embedding_batch_size: int = 16
    embedding_timeout: float = 60.0

    def __post_init__(self):
        for name in ('initial_chips', 'ante', 'target_chips', 'max_utterances',
                     'min_raise', 'context_message_limit', 'memory_round_limit',
                     'max_message_chars', 'event_history_limit', 'round_memo_max_chars', 'game_memo_max_chars',
                     'long_memo_max_chars', 'embedding_context_tokens',
                     'embedding_chunk_max_bytes', 'embedding_batch_size', 'graph_max_attempts',
                     'runner_max_decisions', 'ollama_num_predict', 'router_num_predict', 'router_max_attempts',
                     'openai_max_output_tokens', 'openai_router_max_output_tokens'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f'{name} must be a positive integer')
        if self.sse_heartbeat_seconds <= 0:
            raise ValueError('sse_heartbeat_seconds must be positive')
        if self.initial_chips < self.ante:
            raise ValueError('initial_chips must cover ante without loans')
        if not 0 <= self.min_utterances <= self.max_utterances:
            raise ValueError('min_utterances must be between 0 and max_utterances')
        if self.embedding_chunk_max_bytes < 128:
            raise ValueError('embedding chunk budget must leave room for event headers')
        if self.embedding_chunk_max_bytes > self.embedding_context_tokens - 128:
            raise ValueError('leave at least 128 tokens of conservative embedding headroom')
        if self.ollama_think is not None and type(self.ollama_think) is not bool:
            raise ValueError('ollama_think must be bool or None')
        if self.ollama_usd_per_1k_tokens < 0 or not self.ollama_model.strip():
            raise ValueError('invalid Ollama model or token price')
        if not self.router_model.strip() or self.router_timeout <= 0:
            raise ValueError('invalid router model or timeout')
        if self.embedding_timeout <= 0:
            raise ValueError('embedding_timeout must be positive')
        if self.llm_backend not in {'ollama', 'openai'} or not self.openai_model.strip():
            raise ValueError('invalid LLM backend or OpenAI model')
        if self.openai_router_model is not None and not self.openai_router_model.strip():
            raise ValueError('invalid OpenAI router model')
        if self.openai_reasoning_effort not in {None, 'none', 'low', 'medium', 'high', 'xhigh', 'max'}:
            raise ValueError('invalid OpenAI reasoning effort')
        if self.openai_timeout <= 0 or (self.openai_usd_per_1k_tokens is not None and self.openai_usd_per_1k_tokens < 0):
            raise ValueError('invalid OpenAI timeout or token price')
        object.__setattr__(self, 'data_dir', Path(self.data_dir))

    def memo_limit(self, scope: str) -> int:
        limits = {'round': self.round_memo_max_chars, 'game': self.game_memo_max_chars,
                  'long': self.long_memo_max_chars}
        if scope not in limits:
            raise ValueError('scope must be round, game or long')
        return limits[scope]


def export_mcp_config(config: GameConfig) -> dict[str, str]:
    """기존 MCPClient의 환경 상속을 이용해 subprocess에 같은 설정을 전달한다."""
    values = asdict(config)
    values['data_dir'] = str(config.data_dir.resolve())
    return {'POKER_GAME_CONFIG': json.dumps(values)}


def load_mcp_config() -> GameConfig:
    raw = os.environ.get('POKER_GAME_CONFIG')
    return GameConfig(**json.loads(raw)) if raw else GameConfig()
