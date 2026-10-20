"""실행용 참가자 그래프. 직접 연습할 때는 graph_practice.py를 사용한다.

같은 build_graph()를 참가자별 backend로 구성한다. 테이블이나 다른 참가자를 기다리는
루프를 그래프 안에 넣지 않는다. 한 실행은 한 번의 반응 제안으로 끝난다.
"""
from functools import partial
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .models import AgentDecision, Observation
from .poker_llm import PokerLLM


class GraphInput(TypedDict):
    observation: dict
    event: dict
    force_bet: bool


class GraphOutput(TypedDict):
    decision: dict


class AgentState(GraphInput, total=False):
    route: str
    research: dict
    decision: dict | None
    feedback: str
    attempts: int
    valid: bool


async def route_node(state: AgentState, backend: PokerLLM) -> dict:
    return {'route': backend.route(Observation.model_validate(state['observation']), state['event']),
            'attempts': 0, 'feedback': ''}


async def research_node(state: AgentState, backend: PokerLLM) -> dict:
    result = await backend.research(Observation.model_validate(state['observation']), state['event'])
    return {'research': result}


async def answer_node(state: AgentState, backend: PokerLLM) -> dict:
    """LLM의 형식 오류는 검증 루프로, 통신 오류는 runner의 참가자 실패 처리로 전달."""
    observation = Observation.model_validate(state['observation'])
    try:
        if state['route'] == 'summary':
            result = await backend.summarize_round(observation, state.get('feedback', ''))
        elif state['route'] == 'chat':
            result = await backend.chat(observation, state['event'], state.get('feedback', ''))
        else:
            result = await backend.decide(observation, state.get('research', {}),
                state.get('force_bet', False), state.get('feedback', ''))
        return {'decision': result.model_dump(), 'feedback': ''}
    except ValueError as error:
        return {'decision': None, 'feedback': str(error)}


async def validate_node(state: AgentState, backend: PokerLLM) -> dict:
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
            if decision.channel != 'game':
                raise ValueError('game reaction must not start a casual chat')
            if decision.speech == 'speak' and not observation.can_speak:
                raise ValueError('cannot speak now; choose silent or unavailable')
            if (observation.required_game_utterances > 0 and observation.can_speak
                    and decision.speech != 'speak'):
                raise ValueError('one game utterance is required before betting')
            if decision.bet and decision.bet.action not in observation.allowed_bets:
                raise ValueError('bet is not allowed now')
            if decision.bet and decision.bet.action == 'raise' and decision.bet.amount < backend.config.min_raise:
                raise ValueError('raise increment is too small')
            if state.get('force_bet') and observation.allowed_bets and decision.bet is None:
                raise ValueError('must choose a betting action instead of waiting')
        return {'valid': True, 'attempts': attempts}
    except ValueError as error:
        if attempts >= backend.config.graph_max_attempts:
            raise ValueError(f'invalid decision after {attempts} attempts: {error}') from error
        return {'valid': False, 'feedback': str(error), 'attempts': attempts}


async def remember_node(state: AgentState, backend: PokerLLM) -> dict:
    if state['route'] in {'game', 'summary'}:
        await backend.remember(Observation.model_validate(state['observation']),
                               AgentDecision.model_validate(state['decision']))
    return {}


def build_graph(backend: PokerLLM):
    """참가자별 업무 객체를 주입한 실행용 그래프를 구성한다."""
    graph = StateGraph(AgentState, input_schema=GraphInput, output_schema=GraphOutput)
    graph.add_node('route', partial(route_node, backend=backend))
    graph.add_node('research', partial(research_node, backend=backend))
    graph.add_node('answer', partial(answer_node, backend=backend))
    graph.add_node('validate', partial(validate_node, backend=backend))
    graph.add_node('remember', partial(remember_node, backend=backend))
    graph.add_edge(START, 'route')
    graph.add_conditional_edges('route', lambda state: state['route'],
                                {'chat': 'answer', 'summary': 'answer', 'game': 'research'})
    graph.add_edge('research', 'answer')
    graph.add_edge('answer', 'validate')
    graph.add_conditional_edges('validate', lambda state: 'done' if state['valid'] else 'retry',
                                {'done': 'remember', 'retry': 'answer'})
    graph.add_edge('remember', END)
    return graph.compile()
