"""직접 채우는 그래프 연습장. 완성본 graph.py를 import하거나 대신 실행하지 않는다.

1. 상태와 노드 입출력을 정한다.
2. 각 노드에서 PokerLLMPractice 메서드를 연결한다.
3. build_graph()에서 노드와 edge를 등록한다.
4. 검증 실패의 재시도 상한과 END 조건을 정한다.

완성하기 전 build_graph()와 노드는 명시적으로 NotImplementedError를 낸다.
"""
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .poker_llm_practice import PokerLLMPractice


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
    raise NotImplementedError('연습: route 노드 구현')


async def research_node(state: AgentState, backend: PokerLLMPractice) -> dict:
    """TODO: 필요한 과거 경험을 상태에 넣는다."""
    raise NotImplementedError('연습: research 노드 구현')


async def answer_node(state: AgentState, backend: PokerLLMPractice) -> dict:
    """TODO: 경로별 업무 함수를 호출하고 제안 또는 형식 오류를 상태에 넣는다."""
    raise NotImplementedError('연습: answer 노드 구현')


async def validate_node(state: AgentState, backend: PokerLLMPractice) -> dict:
    """TODO: 제안 검증, 피드백, 재시도 횟수와 실패 종료를 작성한다."""
    raise NotImplementedError('연습: validate 노드 구현')


async def remember_node(state: AgentState, backend: PokerLLMPractice) -> dict:
    """TODO: 검증한 개인 메모 갱신을 저장한다. 공개 행동 적용과 구분한다."""
    raise NotImplementedError('연습: remember 노드 구현')


def build_graph(backend: PokerLLMPractice):
    """TODO: 아래 팩토리를 직접 완성한다.

    사용할 도구: StateGraph, add_node, add_edge, add_conditional_edges, compile.
    backend 주입에는 functools.partial 또는 closure를 사용할 수 있다.
    입력/출력은 위 GraphInput/GraphOutput 계약을 참고한다.
    """
    raise NotImplementedError('연습: 노드 등록과 edge 연결 후 compile 반환')
