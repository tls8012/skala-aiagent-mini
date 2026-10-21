"""실행: .venv/bin/python -m experiments.run --stage 1 --cases 1 --backend fake"""
import argparse
import asyncio
import importlib
import os
from pathlib import Path

import httpx

from agent.ollama_client import OLLAMA_URL
from .common import ExperimentConfig, RunRecorder
from .report import read_rows, summarize


async def run(config):
    recorder = RunRecorder(config)
    try:
        if config.backend == 'ollama':
            with recorder.span('preparation', 'ollama_preflight'):
                response = await asyncio.to_thread(httpx.get, OLLAMA_URL.removesuffix('/api/chat') + '/api/tags', timeout=5)
                response.raise_for_status()
                model = config.model or recorder.game_config('preflight').ollama_model
                found = next((m for m in response.json()['models'] if m['name'] in {model, model + ':latest'}), None)
                if not found:
                    raise ValueError(f'Ollama model is not installed: {model}')
                recorder.manifest.update(model=model, model_digest=found.get('digest'))
                recorder.save_manifest()
        with recorder.span('run', 'all_stages_requested'):
            module = importlib.import_module(f'experiments.stage{config.stage}')
            await module.run(recorder)
        trials = read_rows(recorder.root, 'trials')
        complete = len(trials) == recorder.manifest['planned_trials'] and all(t['valid'] for t in trials)
        recorder.finish('complete' if complete else 'complete_with_failures')
    except BaseException:
        recorder.finish('interrupted_or_failed')
        summarize(recorder.root)
        raise
    summary = summarize(recorder.root)
    print(f'Results: {recorder.root}\nTotal: {summary["total_duration_s"]:.3f}s', flush=True)
    return recorder.root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=int, required=True, choices=(1, 2, 3))
    parser.add_argument('--cases', type=int, default=30)
    parser.add_argument('--backend', choices=('ollama', 'fake'), default='ollama')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--model')
    parser.add_argument('--conditions', nargs='+', default=[])
    parser.add_argument('--output', type=Path, default=Path('experiments/results'))
    parser.add_argument('--timeout', type=float, default=600, help='한 시행의 판단/라운드 제한 시간(초)')
    parser.add_argument('--index-timeout', type=float, default=180)
    args = parser.parse_args()
    os.environ['LANGSMITH_TRACING'] = 'false'
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    try:
        config = ExperimentConfig(**{**vars(args), 'conditions': tuple(args.conditions)})
    except ValueError as error:
        parser.error(str(error))
    result = asyncio.run(run(config))
    return 1 if any(not t['valid'] for t in read_rows(result, 'trials')) else 0


if __name__ == '__main__':
    raise SystemExit(main())
