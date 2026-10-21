"""조건별 정보 접근과 관찰 래퍼. 실제 그래프/LLM/MCP 구현을 재사용한다."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
from uuid import uuid4

from agent.mcp_client import MCPClient
from agent.llm_controller import create_llm
from agents.config import export_mcp_config
from agents.graph import build_graph
from agents.mcp_server import GameTools
from agents.player_llm import PlayerLLM
from agents.poker_llm import PokerLLM
from .common import ACTIVE_DECISION, NodeTimingHandler, digest


class SmokeClient:
    """모델 호출 없는 배선 검사. 결과를 모델 효과의 근거로 사용하지 않는다."""
    name = 'fake:experiment-smoke'

    def complete(self, system, user, schema=None):
        data = json.loads(user)
        obs = data['observation']
        if obs['phase'] == 'revealed':
            return json.dumps({'round': '공개 결과를 확인했다.', 'game': '', 'long': ''}), {'tokens': 0, 'usd': 0}
        required = obs['required_game_utterances']
        speak = obs['can_speak'] and (required > 0 or obs['allowed_bets'])
        bet = None
        if obs['allowed_bets'] and not required:
            action = 'check' if 'check' in obs['allowed_bets'] else 'call'
            size = None
            if data['past_experiences'].get('matches') and 'small' in obs['raise_options']:
                action, size = 'raise', 'small'
            bet = {'action': action, 'raise_size': size}
        return json.dumps({'speech': 'speak' if speak else 'silent' if not required else 'unavailable',
                           'text': '현재 판세와 받은 자료를 검토했어.' if speak else '', 'bet': bet,
                           'wait_for_dialogue': False,
                           'memory_updates': {'round': '현재 관찰을 참고한 잠정 계획이다.', 'game': '', 'long': ''}},
                          ensure_ascii=False), {'tokens': 0, 'usd': 0}


class RecordingClient:
    def __init__(self, client, recorder, trial_id):
        self.client, self.recorder, self.trial_id = client, recorder, trial_id
        self.name = client.name

    def complete(self, system, user, schema=None):
        call_id = uuid4().hex
        self.recorder.active_models.add(call_id)
        try:
            self.recorder.emit('model_calls', event='request', trial_id=self.trial_id, call_id=call_id,
                               model=self.name, system=system, user=user, schema=schema)
            with self.recorder.span('model', 'llm_complete', trial_id=self.trial_id, call_id=call_id):
                content, usage = self.client.complete(system, user, schema=schema)
            self.recorder.emit('model_calls', event='response', trial_id=self.trial_id,
                               call_id=call_id, content=content, usage=usage)
            return content, usage
        except BaseException as error:
            self.recorder.emit('model_calls', event='error', trial_id=self.trial_id,
                               call_id=call_id, error=f'{type(error).__name__}: {error}')
            raise
        finally:
            self.recorder.active_models.discard(call_id)


class LocalSmokeTools:
    def __init__(self, config, evidence):
        self.tools, self.evidence = GameTools(config), evidence

    async def call_tool(self, name, arguments, server='poker'):
        if name == 'search_experiences':
            return deepcopy(self.evidence)
        return getattr(self.tools, name)(**arguments)


@asynccontextmanager
async def memory_connection(config, backend, recorder, trial_id, evidence):
    if backend == 'fake':
        client = LocalSmokeTools(config, evidence)
        try:
            yield client
        finally:
            try:
                with recorder.span('cleanup', 'model_inflight_drain', trial_id=trial_id):
                    await recorder.drain_models()
            finally:
                client.tools.close()
        return
    client = MCPClient(flaky_first_tool_call=False, server_scripts={
        'poker': Path(__file__).resolve().parents[1] / 'agents' / 'mcp_server.py'})
    env = export_mcp_config(config)
    previous = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    try:
        with recorder.span('preparation', 'mcp_start', trial_id=trial_id):
            await client.start()
        yield client
    finally:
        try:
            try:
                with recorder.span('cleanup', 'model_inflight_drain', trial_id=trial_id):
                    await recorder.drain_models()
            finally:
                with recorder.span('cleanup', 'mcp_stop', trial_id=trial_id):
                    await client.stop()
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


class ToolGate:
    def __init__(self, client, recorder, trial_id, *, read_memos, write_memos):
        self.client, self.recorder, self.trial_id = client, recorder, trial_id
        self.read_memos, self.write_memos = read_memos, write_memos

    async def call_tool(self, name, arguments, server='poker'):
        with self.recorder.span('tool', name, trial_id=self.trial_id):
            if name == 'read_memories' and not self.read_memos:
                result = dict.fromkeys(('round', 'game', 'long'), '')
            elif name == 'read_memo' and not self.read_memos:
                result = {'agent_id': arguments['agent_id'], 'scope': arguments['scope'], 'text': ''}
            elif name == 'write_memo' and not self.write_memos:
                result = {'written': False, 'suppressed': True}
            else:
                result = await self.client.call_tool(name, arguments, server=server)
        self.recorder.emit('tools', trial_id=self.trial_id, name=name, arguments=arguments, result=result)
        if name == 'write_memo' and result.get('written'):
            query = {key: arguments[key] for key in ('game_id', 'agent_id', 'round_id', 'scope')}
            with self.recorder.span('audit', 'memo_read_back', trial_id=self.trial_id):
                saved = await self.client.call_tool('read_memo', query, server=server)
            self.recorder.emit('tools', trial_id=self.trial_id, name='memo_read_back', result=saved)
            if saved['text'] != arguments['text']:
                raise ValueError('memo read-back mismatch')
        return result


class ExperimentPokerLLM(PokerLLM):
    def __init__(self, *args, search_enabled, fixed_research=None, recorder, trial_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.search_enabled, self.fixed_research = search_enabled, fixed_research
        self.recorder, self.trial_id = recorder, trial_id

    async def research(self, observation, event):
        if not self.search_enabled:
            result = {'status': 'skipped', 'matches': []}
        elif self.fixed_research is not None:
            result = deepcopy(self.fixed_research)
        else:
            result = await super().research(observation, event)
        self.recorder.emit('research', trial_id=self.trial_id, player_id=self.player_id,
                           basis_event=observation.event_id, response=result,
                           transport='fixture' if self.fixed_research is not None else 'mcp')
        if self.search_enabled and result.get('status') in {'indexing', 'unavailable'}:
            raise RuntimeError(f"search not ready: {result.get('status')}: {result.get('error')}")
        return result


class ObservedGraph:
    def __init__(self, compiled, table, recorder, trial_id):
        self.compiled, self.table, self.recorder, self.trial_id = compiled, table, recorder, trial_id

    async def ainvoke(self, state):
        decision_id = uuid4().hex
        token = ACTIVE_DECISION.set(decision_id)
        try:
            self.recorder.emit('decisions', event='input', trial_id=self.trial_id, player_id='A', state=state)
            with self.recorder.span('graph', 'decision', trial_id=self.trial_id):
                result = await self.compiled.ainvoke(state, config={
                    'callbacks': [NodeTimingHandler(self.recorder, self.trial_id, decision_id)]})
            self.recorder.emit('decisions', event='proposed', trial_id=self.trial_id, player_id='A', result=result)
            key = ('A', digest(result['decision']))
            self.recorder.proposals.setdefault(key, []).append(decision_id)
            return result
        except BaseException as error:
            self.recorder.emit('decisions', event='cancelled' if isinstance(error, asyncio.CancelledError) else 'error', trial_id=self.trial_id,
                               error=f'{type(error).__name__}: {error}')
            raise
        finally:
            ACTIVE_DECISION.reset(token)


def make_graph(table, gate, recorder, trial_id, *, search_enabled, fixed_research=None):
    config = table.config
    client = SmokeClient() if recorder.config.backend == 'fake' else create_llm(config)
    player = PlayerLLM(table, gate, 'A', RecordingClient(client, recorder, trial_id))
    backend = ExperimentPokerLLM(table, gate, 'A', player, search_enabled=search_enabled,
                                fixed_research=fixed_research, recorder=recorder, trial_id=trial_id)
    return ObservedGraph(build_graph(backend), table, recorder, trial_id)


async def prepare_tools(table, client, recorder, trial_id, memos, *, index=False, timeout=180):
    identity = {'game_id': table.game_id, 'agent_id': 'A', 'round_id': table.round.number}
    with recorder.span('preparation', 'context_setup', trial_id=trial_id):
        for scope, text in memos.items():
            await client.call_tool('write_memo', {**identity, 'scope': scope, 'text': text}, server='poker')
        recorder.emit('contexts', trial_id=trial_id, memo_inputs=memos)
        if not index:
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        opponent = next(pid for pid in table.players if pid != 'A')
        query = {**identity, 'query': '상대 주장 블러핑 공개 결과', 'limit': 3, 'opponent_id': opponent}
        result = await client.call_tool('search_experiences', query, server='poker')
        probed = False
        while True:
            status = await client.call_tool('memory_status', identity, server='poker')
            if status.get('error'):
                raise RuntimeError(f'index preparation failed: {status["error"]}')
            if not status['indexing']:
                if probed and result.get('status') == 'ready' and result.get('matches'):
                    recorder.emit('contexts', trial_id=trial_id, index_probe=result, index_status=status)
                    return
                result = await client.call_tool('search_experiences', query, server='poker')
                probed = True
            if result.get('status') == 'unavailable':
                raise RuntimeError(f'index unavailable: {result.get("error")}')
            if loop.time() >= deadline:
                raise TimeoutError('index preparation timed out')
            await asyncio.sleep(0.05)
