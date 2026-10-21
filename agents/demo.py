"""AI-only 그래프 데모. 기본 백엔드는 GameConfig.llm_backend를 따른다."""
import argparse
import asyncio
from dataclasses import replace
import json
import re
from pathlib import Path

from .config import GameConfig
from .graph import build_graph
from .models import AgentDecision, Bet, SpeechIntent, SpeechInterpretation
from .poker_llm import PokerLLM
from .runner import RoundRunner
from .table import Table
from agent.llm_controller import GAME_BACKENDS, configure_backend, configure_model, model_name


class ScriptedPokerLLM(PokerLLM):
    """배선 확인용 고정 정책. fake는 전략/LLM 응답 품질 검증이 아니다."""
    async def research(self, observation, event):
        return {'status': 'skipped', 'matches': []}

    async def chat(self, observation, event, feedback=''):
        return AgentDecision(round_id=observation.round_id, basis_event=observation.event_id,
            speech='speak', text='확인하지 못한 사실이나 비공개 정보는 답할 수 없어.',
            channel='chat', target=event['player_id'], reply_to=event['request_id'])

    async def mixed(self, observation, event, research, force_bet=False, feedback=''):
        if not observation.can_speak:
            return await self.chat(observation, event, feedback)
        result = await self.decide(observation, research, force_bet, feedback)
        return result.model_copy(update={'speech': 'speak',
            'text': '비공개 정보는 답할 수 없어. 게임에서는 상대의 행동을 더 보고 결정할게.',
            'channel': 'mixed', 'target': event['player_id'], 'reply_to': event['request_id']})

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


class ScriptedSpeechInterpreter:
    """외부 호출 없는 fake 배선 검사 전용. 실제 의미 해석은 SpeechInterpreter가 담당한다."""
    async def interpret(self, text, ai_ids, target=None):
        intents = []
        recipients = [target] if target in ai_ids else list(ai_ids)
        for fragment in filter(None, (part.strip() for part in re.split(r'(?<=[?!.])\s+', text))):
            named = [pid for pid in ai_ids if re.search(re.escape(pid) + r'(?:야|아|[, :])', fragment)]
            recipients = named or recipients
            kind = ('private_request' if any(word in fragment for word in ('프롬프트', '비밀', '개인 메모'))
                    else 'chat' if any(word in fragment for word in ('날씨', '안녕')) else 'game')
            intents.append(SpeechIntent(kind=kind, targets=recipients, text=fragment))
        return SpeechInterpretation(intents=intents)


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
    config = configure_model(configure_backend(GameConfig(), args.mode), args.model)
    changes = {key: value for key, value in {
        'seed': args.seed, 'data_dir': args.data_dir,
        'runner_max_decisions': args.max_decisions}.items() if value is not None}
    config = replace(config, **changes)
    table = Table([f'AI{i + 1}' for i in range(args.players)], config=config)
    mcp = None
    try:
        if args.mode != 'fake' or args.publish_memory:
            from agent.mcp_client import MCPClient
            # Table과 subprocess가 같은 설정/저장 루트를 사용하게 한다.
            from .config import export_mcp_config
            import os
            os.environ.update(export_mcp_config(config))
            mcp = MCPClient(flaky_first_tool_call=False, server_scripts={
                'poker': Path(__file__).with_name('mcp_server.py').resolve()})
            await mcp.start()
        if args.mode != 'fake':
            from .player_llm import PlayerLLM
            backends = {pid: PokerLLM(table, mcp, pid, PlayerLLM(table, mcp, pid)) for pid in table.players}
        else:
            backends = {pid: ScriptedPokerLLM(table, mcp, pid, None) for pid in table.players}
        graphs = {pid: build_graph(backend) for pid, backend in backends.items()}
        print(f'모드={args.mode}, 모델={model_name(config)}, 참가자={args.players}', flush=True)
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
    from dotenv import load_dotenv
    load_dotenv()
    parser.add_argument('--mode', choices=GAME_BACKENDS, default=GameConfig().llm_backend)
    parser.add_argument('--players', type=int, default=3)
    parser.add_argument('--rounds', type=int, default=1)
    parser.add_argument('--model', help='선택한 백엔드의 config 모델을 재정의')
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
