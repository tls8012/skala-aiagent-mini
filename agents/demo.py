"""AI-only 그래프 데모. 기본은 외부 모델 없이 실행하는 fake 모드."""
import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path

from .config import GameConfig
from .graph import build_graph
from .models import AgentDecision, Bet
from .poker_llm import PokerLLM
from .runner import RoundRunner
from .table import Table


class ScriptedPokerLLM(PokerLLM):
    """배선 확인용 고정 정책. fake는 전략/LLM 응답 품질 검증이 아니다."""
    async def research(self, observation, event):
        return {'status': 'skipped', 'matches': []}

    async def summarize_round(self, observation, feedback=''):
        return AgentDecision(round_id=observation.round_id, basis_event=observation.event_id,
                             speech='unavailable')

    async def decide(self, observation, research, force_bet=False, feedback=''):
        speak = observation.can_speak and observation.required_game_utterances > 0
        bet = None
        if observation.allowed_bets:
            action = 'check' if 'check' in observation.allowed_bets else 'call'
            bet = Bet(action=action)
        return AgentDecision(round_id=observation.round_id, basis_event=observation.event_id,
            speech='speak' if speak else 'silent' if observation.required_game_utterances == 0 else 'unavailable',
            text='상대의 말과 행동을 보고 결정하겠어.' if speak else '', bet=bet)


def print_event(event):
    kind = event['type']
    if kind == 'RoundStarted':
        print(f"\n라운드 {event['round_id']} 시작: {' → '.join(event['order'])}", flush=True)
    elif kind == 'Speech':
        print(f"[{event['player_id']}] {event['text']}", flush=True)
    elif kind == 'BetPlaced':
        print(f"[{event['player_id']}] {event['action']} {event.get('raise_size') or ''} +{event['amount']}", flush=True)
    elif kind == 'AgentFailed':
        print(f"[{event['player_id']}] {event['message']}", flush=True)
    elif kind == 'RoundEnded':
        print('공개 결과:', json.dumps(event['revealed_cards'], ensure_ascii=False), flush=True)


async def run_demo(args):
    config = GameConfig()
    changes = {key: value for key, value in {
        'ollama_model': args.model, 'seed': args.seed, 'data_dir': args.data_dir,
        'runner_max_decisions': args.max_decisions}.items() if value is not None}
    config = replace(config, **changes)
    table = Table([f'AI{i + 1}' for i in range(args.players)], config=config)
    mcp = None
    try:
        if args.mode == 'ollama' or args.publish_memory:
            from agent.mcp_client import MCPClient
            # Table과 subprocess가 같은 설정/저장 루트를 사용하게 한다.
            from .config import export_mcp_config
            import os
            os.environ.update(export_mcp_config(config))
            mcp = MCPClient(flaky_first_tool_call=False, server_scripts={
                'poker': Path(__file__).with_name('mcp_server.py').resolve()})
            await mcp.start()
        if args.mode == 'ollama':
            from .player_llm import PlayerLLM
            backends = {pid: PokerLLM(table, mcp, pid, PlayerLLM(table, mcp, pid)) for pid in table.players}
        else:
            backends = {pid: ScriptedPokerLLM(table, mcp, pid, None) for pid in table.players}
        graphs = {pid: build_graph(backend) for pid, backend in backends.items()}
        print(f'모드={args.mode}, 모델={config.ollama_model}, 참가자={args.players}', flush=True)
        runner = RoundRunner(table, graphs, on_event=print_event)
        for _ in range(args.rounds):
            result = await runner.run_round(mcp=mcp, publish_memory=args.publish_memory)
            print(f"승자={result['winner'] or '무승부'}, 판돈={result['pot']}, 판단 실행={runner.decisions}")
            if runner.errors:
                print('에이전트 오류:', json.dumps(runner.errors, ensure_ascii=False))
            if table.finished:
                break
        print('기록:', table.archive_dir)
    finally:
        if mcp is not None:
            await mcp.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['fake', 'ollama'], default='fake')
    parser.add_argument('--players', type=int, default=3)
    parser.add_argument('--rounds', type=int, default=1)
    parser.add_argument('--model', help='기본값은 config.ollama_model')
    parser.add_argument('--seed', type=int)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--max-decisions', type=int)
    parser.add_argument('--publish-memory', action='store_true', help='라운드 기록을 MCP에 전송해 색인 예약')
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error('--rounds must be positive')
    asyncio.run(run_demo(args))


if __name__ == '__main__':
    main()
