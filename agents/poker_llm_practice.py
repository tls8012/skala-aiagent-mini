"""연습용 업무 계층. 완성본 poker_llm.py와 독립적이며 실제 호출은 구현 전까지 없다.

추천 순서: route → decide → chat → research → remember → summarize_round.
아래 메서드의 TODO를 직접 채운 뒤 graph_practice.py에 연결한다.
PlayerLLM.complete()를 사용하면 세 개인 메모는 자동으로 붙는다.
"""
from .models import AgentDecision, Observation


class PokerLLMPractice:
    def __init__(self, table, mcp, player_id: str, llm):
        # 의존성 보관만 한다. 클라이언트 생성이나 네트워크 호출은 하지 않는다.
        self.table = table
        self.mcp = mcp
        self.player_id = player_id
        self.llm = llm
        self.config = table.config

    def route(self, observation: Observation, event: dict) -> str:
        """TODO: 'game' / 'chat' / 'summary' 중 경로를 반환한다.

        입력: 참가자 관찰 정보, 계기가 된 테이블 사건.
        고려할 것: 지목된 인간 잡담인가? 공개가 끝났는가?
        """
        raise NotImplementedError('연습: 경로 분류 구현')

    async def research(self, observation: Observation, event: dict) -> dict:
        """TODO: 검색 필요 여부를 정하고 MCP의 search_experiences를 호출한다.

        출력 예시 형식: {'status': ..., 'matches': [...]}.
        출처 ID는 Table.call_player_tool()에 맡긴다.
        """
        raise NotImplementedError('연습: 경험 조회 구현')

    async def chat(self, observation: Observation, event: dict, feedback: str = '') -> AgentDecision:
        """TODO: 인간 질문에 답하고 channel='chat'인 제안을 반환한다.

        question request_id와 reply_to의 연결, 일반 잡담의 도구 사용 범위를 생각해본다.
        """
        raise NotImplementedError('연습: 일반 대화 응답 구현')

    async def decide(self, observation: Observation, research: dict,
                     force_bet: bool = False, feedback: str = '') -> AgentDecision:
        """TODO: 프롬프트·JSON 출력 스키마·파싱을 작성한다.

        발언과 베팅은 독립적이다. 현재 관찰 정보에 없는 자기 카드를 사용하면 안 된다.
        round_id/basis_event는 실행 코드가 붙이고, 검증 피드백도 다음 호출에 전달한다.
        """
        raise NotImplementedError('연습: 게임 판단 구현')

    async def remember(self, observation: Observation, decision: AgentDecision):
        """TODO: 판단의 memory_updates를 개인 메모에 반영한다.

        빈 갱신의 의미, 글자 수 상한, 실행 전 잠정 판단이라는 점을 고려한다.
        """
        raise NotImplementedError('연습: 개인 메모 갱신 구현')

    async def summarize_round(self, observation: Observation, feedback: str = '') -> AgentDecision:
        """TODO: 공개 결과로 회고하고 메모만 갱신하는 제안을 반환한다.

        라운드 종료 후 발언/베팅은 금지된다. 폴드 카드는 추측으로 채우지 않는다.
        """
        raise NotImplementedError('연습: 라운드 회고 구현')
