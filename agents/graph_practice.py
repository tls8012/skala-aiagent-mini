"""직접 채우는 그래프 연습장. 완성본 graph.py를 import하거나 대신 실행하지 않는다.

1. 상태와 노드 입출력을 정한다.
2. 각 노드에서 PokerLLMPractice 메서드를 연결한다.
3. build_graph()에서 노드와 edge를 등록한다.
4. 검증 실패의 재시도 상한과 END 조건을 정한다.

완성하기 전 build_graph()와 노드는 명시적으로 NotImplementedError를 낸다.
"""
from typing import TypedDict
from functools import partial

from langgraph.graph import END, START, StateGraph

from .poker_llm_practice import PokerLLMPractice
from .models import AgentDecision, Observation

class GraphInput(TypedDict):
    observation: dict
    event: dict
    force_bet: bool


class GraphOutput(TypedDict):
    decision: dict


class AgentState(GraphInput, total=False):
    # TODO: 필요한 상태를 추가하거나 변경한다.
    route: str
    research: dict
    decision: dict | None
    feedback: str
    attempts: int
    valid: bool


async def route_node(state: AgentState, backend: PokerLLMPractice) -> dict:
    """TODO: 관찰 정보를 읽고 다음 경로를 상태에 기록한다."""
    to_rt = await backend.route(Observation.model_validate(state['observation']), state['event'])
    return {'route':to_rt,
            'attempts':0, 'feedback':''}


async def research_node(state: AgentState, backend: PokerLLMPractice) -> dict:
    """TODO: 필요한 과거 경험을 상태에 넣는다."""
    to_rt = await backend.research(Observation.model_validate(state['observation']), state['event'])
    return {'result':to_rt}


async def answer_node(state: AgentState, backend: PokerLLMPractice) -> dict:
    """TODO: 경로별 업무 함수를 호출하고 제안 또는 형식 오류를 상태에 넣는다."""
    obs = Observation.model_validate(state['observation'])
    try:
        if state['route']=='summary':
            to_rt = await backend.summarize_round(obs, state.get('feedback', ''))
        elif state['route']=='chat':
            to_rt = await backend.chat(obs, state['event'], state.get('feedback', ''))
        elif state['route']=='mixed':
            to_rt = await backend.mixed(obs, state['event'], state.get('research', {}),
                                        state.get('force_bet', False), state.get('feedback', ''))
        else:
            to_rt = await backend.decide(obs, state.get('research', {}),
                state.get('force_bet', False), state.get('feedback', ''))
        return {'decision':to_rt.model_dump(), 'feedback':''}
    except ValueError as e:
        return {'decision':None, 'feedback':str(e)}


async def validate_node(state: AgentState, backend: PokerLLMPractice) -> dict:
    """TODO: 제안 검증, 피드백, 재시도 횟수와 실패 종료를 작성한다."""
    attempts = state.get('attempts', 0) + 1
    observation = Observation.model_validate(state['observation'])
    try:
        if state.get('decision') is None:
            raise ValueError(state.get('feedback') or 'missing decision')
        decision = AgentDecision.model_validate(state['decision'])
        if decision.round_id != observation.round_id or decision.basis_event != observation.event_id:
            raise ValueError('decision must refer to this observation')
        if len(decision.text) > backend.config.max_message_chars:
            raise ValueError('speech is too long')
        for scope, text in decision.memory_updates.model_dump().items():
            if len(text) > backend.config.memo_limit(scope):
                raise ValueError(f'{scope} memo is too long')
        if state['route'] == 'summary':
            if observation.phase != 'revealed' or decision.speech != 'unavailable' or decision.bet is not None:
                raise ValueError('summary must only update memories after reveal')
        elif state['route'] == 'chat':
            if (decision.channel != 'chat' or decision.reply_to != state['event']['request_id']
                    or decision.target != state['event']['player_id'] or decision.bet is not None):
                raise ValueError('chat must answer the addressed human request only')
        else:
            if state['route'] == 'mixed':
                if decision.speech != 'speak':
                    raise ValueError('answer the addressed human request')
                if (
                        decision.channel not in {'mixed', 'chat'} or
                        decision.reply_to != state['event']['request_id'] or
                        decision.target != state['event']['player_id']):
                    raise ValueError('mixed reaction must answer the original human request')
            elif decision.channel != 'game':
                raise ValueError('game reaction must not start a casual chat')
            game_speech = decision.speech == 'speak' and decision.channel != 'chat'
            if game_speech and not observation.can_speak:
                raise ValueError('cannot speak now; choose silent or unavailable')
            if (observation.required_game_utterances > 0 and observation.can_speak
                    and (decision.speech != 'speak' or decision.channel == 'chat')):
                raise ValueError('one game utterance is required before betting')
            if decision.bet and observation.required_game_utterances > 0:
                raise ValueError('minimum game speech must be met before betting')
            if decision.bet and decision.bet.action not in observation.allowed_bets:
                raise ValueError('bet is not allowed now')
            if decision.bet and decision.bet.action == 'raise' and decision.bet.raise_size not in observation.raise_options:
                raise ValueError('raise size is not available')
            if state.get('force_bet') and observation.allowed_bets and decision.bet is None:
                raise ValueError('must choose a betting action instead of waiting')
        return {'valid': True, 'attempts': attempts}
    except ValueError as error:
        if attempts >= backend.config.graph_max_attempts:
            raise ValueError(f'invalid decision after {attempts} attempts: {error}') from error
        return {'valid': False, 'feedback': str(error), 'attempts': attempts}


async def remember_node(state: AgentState, backend: PokerLLMPractice) -> dict:
    """TODO: 검증한 개인 메모 갱신을 저장한다. 공개 행동 적용과 구분한다."""
    if state['route'] in ['game', 'mixed', 'summary']:
        await backend.remember(observation=Observation.model_validate(state['observation']),
                            decision=AgentDecision.model_validate(state['decision']))
    return {}


def build_graph(backend: PokerLLMPractice):
    """TODO: 아래 팩토리를 직접 완성한다.

    사용할 도구: StateGraph, add_node, add_edge, add_conditional_edges, compile.
    backend 주입에는 functools.partial 또는 closure를 사용할 수 있다.
    입력/출력은 위 GraphInput/GraphOutput 계약을 참고한다.
    """
    graph = StateGraph(AgentState, input_schema=GraphInput, output_schema=GraphOutput)
    graph.add_node("route_node", partial(route_node, backend=backend))
    graph.add_node("research_node", partial(research_node, backend=backend))
    graph.add_node("answer_node", partial(answer_node, backend=backend))
    graph.add_node("validate_node", partial(validate_node, backend=backend))
    graph.add_node("remember_node", partial(remember_node, backend=backend))

    graph.add_edge(START, "route_node")
    graph.add_conditional_edges("route_node", lambda state: state["route"], 
                                {'chat':'answer_node', 'summary':'answer_node', 
                                 'game':'research_node', 'research':'research_node'})
    graph.add_edge("research_node", "answer_node")
    graph.add_edge("answer_node", "validate_node")
    graph.add_conditional_edges("validate_node", lambda x: '1' if x['state'] is 'valid' else '0',
                                {'1':'remember_node', '0':'answer_node'})
    graph.add_edge("remember_node", END)
    return graph.compile()