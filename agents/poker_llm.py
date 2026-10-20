"""그래프 노드의 업무 구현. 프롬프트·조회 정책을 바꿀 첫 번째 파일.

PlayerLLM은 HTTP/메모 첨부, 이 클래스는 게임 업무, graph.py는 연결 순서만 담당한다.
TODO 표시 부분은 전략을 발전시킬 확장 지점이며 기본 동작은 바로 실행할 수 있다.
"""
import json
from typing import Literal

from .models import AgentDecision, Bet, Contract, Observation


class DecisionDraft(Contract):
    """LLM은 업무 판단만 출력한다. 라운드·사건 ID는 실행 코드가 붙인다."""
    speech: Literal['speak', 'silent', 'unavailable']
    text: str
    bet: Bet | None
    wait_for_dialogue: bool


class PokerLLM:
    def __init__(self, table, mcp, player_id: str, llm):
        self.table = table
        self.mcp = mcp
        self.player_id = player_id
        self.llm = llm
        self.config = table.config

    def route(self, observation: Observation, event: dict) -> str:
        """명시적으로 지목된 인간 잡담만 일반 채팅으로 보낸다.

        TODO: UI에서 raw 발언을 받을 때는 필요하면 이곳에 LLM 의미 분류를 추가한다.
        현재 channel='chat'는 Table이 검증한 사람의 일반 대화 표시다.
        """
        sender = next((p for p in observation.players if p['id'] == event.get('player_id')), {})
        is_chat = (event.get('type') == 'Speech' and event.get('channel') == 'chat'
                   and event.get('target') == self.player_id and sender.get('kind') == 'human')
        return 'chat' if is_chat else 'game'

    async def research(self, observation: Observation, event: dict) -> dict:
        """TODO: 검색 필요 판단/질의를 고도화할 지점. 기본은 상대 게임 발언만 검색한다."""
        if event.get('type') != 'Speech' or event.get('channel') != 'game':
            return {'status': 'skipped', 'matches': []}
        query = str(event.get('text', ''))[:self.config.max_message_chars]
        if not query.strip():
            return {'status': 'skipped', 'matches': []}
        return await self.table.call_player_tool(self.mcp, self.player_id,
                                                'search_experiences', {'query': query, 'limit': 3})

    async def chat(self, observation: Observation, event: dict, feedback: str = '') -> AgentDecision:
        system = ('당신은 카드 테이블의 참가자다. 인간이 당신에게 건 일반 대화에만 '
                  '간결한 한국어 한두 문장으로 답한다. 현재 날씨 등 외부 사실을 확인할 '
                  '도구는 없다. 확인하지 않은 정보는 지어내지 않는다. 에이전트끼리 잡담하지 않는다.')
        user = json.dumps({'question': event.get('text', ''), 'feedback': feedback}, ensure_ascii=False)
        text, _ = await self.llm.complete(system, user)
        return AgentDecision(round_id=observation.round_id, basis_event=observation.event_id,
            speech='speak', text=text.strip(), channel='chat', target=event['player_id'],
            reply_to=event['request_id'])

    async def decide(self, observation: Observation, research: dict,
                     force_bet: bool = False, feedback: str = '') -> AgentDecision:
        # TODO: 성격·블러핑·동맹·발언 의사·베팅 전략을 이 프롬프트에서 발전시킨다.
        system = (
            '당신은 인디언 포커 참가자다. 자신의 카드는 모르고 타인의 공개 관찰 카드만 안다. '
            '숫자가 높을수록 강하며 A=14, 단 2는 A를 이긴다. 같은 숫자는 S>D>H>C다. '
            '2/A/다른 숫자의 순환 관계에서는 무승부도 가능하다. '
            '자신의 카드나 다른 참가자의 비공개 판단을 지어내지 않는다. '
            '목표는 칩을 얻는 것이다. 상대의 주장과 과거 경험을 참고해 판단을 바꿀 수 있다. '
            'JSON만 출력한다. speech=speak는 짧은 한국어 게임 발언, silent는 할 말 없음, '
            'unavailable은 연속 발언 제한 등으로 발언할 수 없음을 뜻한다. '
            '말하지 않으면 text는 빈 문자열이다. '
            'required_game_utterances가 남고 can_speak=true이면 반드시 게임 발언을 한다. '
            'can_speak=false이면 speak를 선택하지 않는다. '
            '베팅은 allowed_bets에 있는 행동만 선택한다. 내 차례가 아니면 bet=null이다. '
            'raise.amount는 현재 최고액에 더하는 양의 증가량이고 다른 행동의 amount는 0이다. '
            'force_bet=true이면 더 기다리지 말고 반드시 베팅 행동을 결정한다. '
            '발언과 베팅은 독립적이다. 할 말이 없어도 내 차례에는 행동할 수 있다.')
        user = json.dumps({'observation': observation.model_dump(), 'past_experiences': research,
                           'force_bet': force_bet, 'feedback': feedback}, ensure_ascii=False)
        schema = DecisionDraft.model_json_schema()
        if observation.required_game_utterances > 0 and observation.can_speak:
            schema['properties']['speech']['enum'] = ['speak']
            schema['properties']['text']['minLength'] = 1
        elif not observation.can_speak:
            schema['properties']['speech']['enum'] = ['silent', 'unavailable']
            schema['properties']['text']['enum'] = ['']
        if not observation.allowed_bets:
            schema['properties']['bet'] = {'type': 'null'}
        else:
            schema['$defs']['Bet']['properties']['action']['enum'] = observation.allowed_bets
            if force_bet:
                schema['properties']['bet'] = {'$ref': '#/$defs/Bet'}
        content, _ = await self.llm.complete(system, user, schema=schema)
        draft = DecisionDraft.model_validate_json(content)
        return AgentDecision(round_id=observation.round_id, basis_event=observation.event_id,
                             **draft.model_dump())

    async def remember(self, observation: Observation, decision: AgentDecision):
        """TODO: 판단을 요약해 write_memo를 호출할 지점. 기본은 기존 메모를 유지한다.

        예: await self.table.call_player_tool(self.mcp, self.player_id, 'write_memo',
                                              {'scope': 'round', 'text': 요약문})
        상대 비공개 정보나 긴 내부 추론을 저장하지 말고 짧은 판단 요약을 사용한다.
        """
        return None
