"""실행: .venv/bin/python -m experiments.run --stage 1 --cases 1 --backend fake"""
import argparse
import asyncio
import importlib
import os
from pathlib import Path

from agent.llm_controller import GAME_BACKENDS, check_backend
from agents.config import GameConfig
from .common import ExperimentConfig, RunRecorder
from .report import read_rows, summarize


async def run(config):
    recorder = RunRecorder(config)
    try:
        if config.backend != 'fake':
            with recorder.span('preparation', 'llm_preflight'):
                settings = recorder.game_config('preflight')
                recorder.manifest.update(await asyncio.to_thread(check_backend, settings))
                if settings.llm_backend == 'openai' and settings.openai_reasoning_effort != 'none':
                    recorder.manifest['temperature'] = None
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
    from dotenv import load_dotenv
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=int, required=True, choices=(1, 2, 3, 4))
    parser.add_argument('--cases', type=int, help='상황 수: 1~3단계 기본 30, 4단계 기본 10개 배패')
    parser.add_argument('--backend', choices=GAME_BACKENDS, default=GameConfig().llm_backend)
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
