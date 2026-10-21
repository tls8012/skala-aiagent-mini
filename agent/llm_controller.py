"""LLM 생성/모델 선택의 단일 진입점. 업무 코드는 공급자별 클라이언트를 직접 만들지 않는다."""
from dataclasses import replace
from typing import Protocol

import httpx

from .ollama_client import OLLAMA_URL, OllamaLLM
from .openai_client import OpenAILLM

LIVE_BACKENDS = ('ollama', 'openai')
GAME_BACKENDS = ('fake', *LIVE_BACKENDS)


class LLMClient(Protocol):
    name: str

    def complete(self, system: str, user: str, schema: dict | None = None) -> tuple[str, dict]: ...

    async def stream(self, system: str, user: str, on_token=None) -> tuple[str, dict]: ...


def configure_backend(config, backend):
    if backend not in GAME_BACKENDS:
        raise ValueError('LLM_BACKEND must be fake, ollama or openai')
    return replace(config, llm_backend=backend) if backend != 'fake' and config.llm_backend != backend else config


def model_name(config, role='player'):
    if role not in ('player', 'router'):
        raise ValueError('LLM role must be player or router')
    if config.llm_backend == 'ollama':
        return config.router_model if role == 'router' else config.ollama_model
    if config.llm_backend == 'openai':
        return (config.openai_router_model or config.openai_model) if role == 'router' else config.openai_model
    raise ValueError(f'unknown LLM backend: {config.llm_backend}')


def configure_model(config, model):
    if model is None:
        return config
    key = 'openai_model' if config.llm_backend == 'openai' else 'ollama_model'
    return replace(config, **{key: model})


def create_llm(config, role='player') -> LLMClient:
    model = model_name(config, role)
    if config.llm_backend == 'ollama':
        return OllamaLLM(model, config.ollama_usd_per_1k_tokens,
                         think=False if role == 'router' else config.ollama_think,
                         num_predict=config.router_num_predict if role == 'router' else config.ollama_num_predict)
    return OpenAILLM(model, max_output_tokens=config.openai_router_max_output_tokens if role == 'router' else config.openai_max_output_tokens,
                     reasoning_effort=config.openai_reasoning_effort, timeout=config.openai_timeout,
                     usd_per_1k_tokens=config.openai_usd_per_1k_tokens)


def check_backend(config):
    """실험 시작 전 읽기 전용 점검. API 키나 요청 헤더를 반환하지 않는다."""
    model = model_name(config)
    if config.llm_backend == 'openai':
        info = create_llm(config).model_info()
        return {'model': model, 'model_digest': None, 'provider_model_id': info['id']}
    response = httpx.get(OLLAMA_URL.removesuffix('/api/chat') + '/api/tags', timeout=5)
    response.raise_for_status()
    found = next((m for m in response.json()['models'] if m['name'] in {model, model + ':latest'}), None)
    if not found:
        raise ValueError(f'Ollama model is not installed: {model}')
    return {'model': model, 'model_digest': found.get('digest')}
