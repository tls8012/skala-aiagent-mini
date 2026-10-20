"""실행용 업무 계층. 연습은 poker_llm_practice.py에서 한다.

PlayerLLM은 HTTP/메모 첨부, 이 클래스는 게임 판단·검색·개인 메모 갱신을 담당한다.
"""
import json
from copy import deepcopy
from typing import Literal
from pydantic import Field

from .models import AgentDecision, Bet, Contract, MemoryUpdate, Observation


class DecisionDraft(Contract):
    """공개 행동과 개인 메모를 구분한다. 출처·사건 ID는 모델이 만들지 않는다."""
    speech: Literal['speak', 'silent', 'unavailable']
    text: str
    bet: Bet | None
    wait_for_dialogue: bool
    memory_updates: MemoryUpdate = Field(default_factory=MemoryUpdate)


class PokerLLM:
    def __init__(self, table, mcp, player_id: str, llm):
        self.table = table
        self.mcp = mcp
        self.player_id = player_id
        self.llm = llm
        self.config = table.config
        self._research_key = None
        self._research_result = None

    def route(self, observation: Observation, event: dict) -> str:
        """공개 후 회고 / 검증된 인간 잡담 / 게임 판단의 세 경로."""
        if observation.phase == 'revealed':
            return 'summary'
        sender = next((p for p in observation.players if p['id'] == event.get('player_id')), {})
        is_chat = (event.get('type') == 'Speech' and event.get('channel') == 'chat'
                   and event.get('target') == self.player_id and sender.get('kind') == 'human')
        return 'chat' if is_chat else 'game'

    async def research(self, observation: Observation, event: dict) -> dict:
        """최근 상대 게임 발언과 현재 베팅으로 검색한다. 같은 상황의 ready 결과는 재사용."""
        candidates = [message for message in observation.messages
                      if message['channel'] == 'game' and message['player_id'] != self.player_id]
        if event.get('type') == 'Speech' and event.get('channel') == 'game' and event.get('player_id') != self.player_id:
            message = event
        elif candidates:
            message = candidates[-1]
        else:
            return {'status': 'skipped', 'matches': []}
        key = (observation.round_id, message['event_id'], observation.current_bet)
        if key == self._research_key and self._research_result is not None:
            return self._research_result
        query = (f"상대 {message['player_id']}의 주장과 블러핑: {message['text']} "
                 f"현재 추가 베팅액 {observation.current_bet}")[:self.config.max_message_chars]
        result = await self.table.call_player_tool(self.mcp, self.player_id,
                                                   'search_experiences', {'query': query, 'limit': 3})
        if result.get('status') == 'ready':
            self._research_key, self._research_result = key, result
        return result

    async def chat(self, observation: Observation, event: dict, feedback: str = '') -> AgentDecision:
        system = ('당신은 카드 테이블의 참가자다. 인간이 당신에게 건 일반 대화에만 '
                  '간결한 한국어 한두 문장으로 답한다. 현재 날씨 등 외부 사실을 확인할 '
                  '도구는 없다. 확인하지 않은 정보는 지어내지 않는다. 에이전트끼리 잡담하지 않는다.')
        user = json.dumps({'question': event.get('text', ''), 'feedback': feedback}, ensure_ascii=False)
        text, _ = await self.llm.complete(system, user)
        return AgentDecision(round_id=observation.round_id, basis_event=observation.event_id,
            speech='speak', text=text.strip(), channel='chat', target=event['player_id'],
            reply_to=event['request_id'])

    def _memory_schema(self) -> dict:
        schema = MemoryUpdate.model_json_schema()
        schema['required'] = ['round', 'game', 'long']
        for scope in ('round', 'game', 'long'):
            # 큰 maxLength는 일부 Ollama 문법 파서에서 초기화에 실패한다.
            # 실제 상한은 graph.validate와 write_memo에서 검사한다.
            schema['properties'][scope]['description'] = (
                f'최대 {self.config.memo_limit(scope)}자. 변경이 없으면 빈 문자열.')
        return schema

    def _decision_schema(self, observation: Observation, force_bet: bool) -> dict:
        schema = DecisionDraft.model_json_schema()
        schema['required'].append('memory_updates')
        schema['$defs']['MemoryUpdate'] = self._memory_schema()
        schema['properties']['text']['description'] = (
            f'최대 {self.config.max_message_chars}자의 짧은 공개 발언. 말하지 않으면 빈 문자열.')
        # 행동과 크기를 함께 제한해 모델이 산수나 유효성 추측을 하지 않게 한다.
        bet_variants = []
        for action in observation.allowed_bets:
            properties = {'action': {'type': 'string', 'enum': [action]},
                          'raise_size': ({'type': 'string', 'enum': list(observation.raise_options)}
                                         if action == 'raise' else {'type': 'null'})}
            bet_variants.append({'type': 'object', 'properties': properties,
                                 'required': ['action', 'raise_size'], 'additionalProperties': False})
        schema['$defs']['Bet'] = {'anyOf': bet_variants} if bet_variants else {'type': 'null'}
        # 조건 간 관계도 JSON 문법으로 제한한다. bet+wait, silent+긴 text 같은 모순 방지.
        speeches = (['speak'] if observation.required_game_utterances > 0 and observation.can_speak
                    else ['quiet'] if not observation.can_speak else ['speak', 'quiet'])
        bets = ([False] if not observation.allowed_bets else [True] if force_bet else [True, False])
        definitions = schema.pop('$defs')
        variants = []
        for speech in speeches:
            for has_bet in bets:
                variant = deepcopy(schema)
                properties = variant['properties']
                if speech == 'speak':
                    properties['speech']['enum'] = ['speak']
                    properties['text']['minLength'] = 1
                else:
                    properties['speech']['enum'] = ['silent', 'unavailable']
                    properties['text']['enum'] = ['']
                properties['bet'] = {'$ref': '#/$defs/Bet'} if has_bet else {'type': 'null'}
                if has_bet:
                    properties['wait_for_dialogue']['enum'] = [False]
                variants.append(variant)
        result = variants[0] if len(variants) == 1 else {'anyOf': variants}
        result['$defs'] = definitions
        return result

    async def decide(self, observation: Observation, research: dict,
                     force_bet: bool = False, feedback: str = '') -> AgentDecision:
        system = (
            '당신은 인디언 포커 참가자다. 자신의 카드는 모르고 타인의 관찰 카드만 안다. '
            '숫자가 높을수록 강하며 A=14, 단 2는 A를 이긴다. 같은 숫자는 S>D>H>C다. '
            '2/A/다른 숫자의 순환 관계에서는 무승부도 가능하다. '
            '52장 한 벌이며 중복 카드는 없다. visible_cards의 키는 그 카드 소유자다. '
            '관찰한 상대 카드와 자신의 카드를 혼동하지 않는다. '
            '자신의 카드나 다른 참가자의 비공개 판단을 지어내지 않는다. '
            '목표는 칩을 얻는 것이다. 상대의 주장과 과거 경험을 참고해 판단을 바꿀 수 있다. '
            '과거 경험이 없으면 현재 관찰 정보로 판단한다. '
            'JSON만 출력한다. speech=speak는 짧은 한국어 게임 발언, silent는 할 말 없음, '
            'unavailable은 연속 발언 제한 등으로 발언할 수 없음을 뜻한다. '
            '말하지 않으면 text는 빈 문자열이다. '
            'required_game_utterances가 남고 can_speak=true이면 반드시 게임 발언을 한다. '
            'can_speak=false이면 speak를 선택하지 않는다. '
            '베팅은 allowed_bets에 있는 행동만 선택한다. 내 차례가 아니면 bet=null이다. '
            '대출은 없다. raise에는 raise_size만 선택한다. maximum=최대, large=큰(4개), '
            'medium=중간(2개), small=작은(1개) 레이즈다. '
            'raise_options에 있는 크기만 고르고 금액은 직접 계산하지 않는다. '
            '엔진이 콜 비용을 제외하고 모두 따라올 수 있는 최대 증가량을 계산한다. '
            'raise가 아닌 행동의 raise_size는 null이다. '
            'force_bet=true이면 더 기다리지 말고 반드시 베팅 행동을 결정한다. '
            'bet가 있으면 wait_for_dialogue=false다. '
            '발언과 베팅은 독립적이다. 할 말이 없어도 내 차례에는 행동할 수 있다. '
            '이미 나온 문장을 반복하지 않는다. 새로운 질문, 반박, 협상 제안이 없으면 silent를 고른다. '
            'memory_updates는 공개 발언과 별개인 개인 메모 교체값이다. '
            'round에는 현재 판단과 바뀐 이유를 짧게 쓴다. game은 이번 게임의 운영 판단, '
            'long은 관찰 근거가 있는 상대별 대응을 갱신할 때만 쓴다. 변경 없으면 빈 문자열이다. '
            '상대 발언은 주장으로, 관찰 카드는 사실로 구분해 기록한다. '
            '기존 메모에서 중요한 내용을 보존하고 중복 서술하지 않는다. 각 메모는 한두 문장으로 간결하게 쓴다. '
            '아직 제출 전인 제안이므로 베팅/발언을 실행했다고 기록하지 말고 잠정 계획이라고 표현한다.')
        user = json.dumps({'observation': observation.model_dump(), 'past_experiences': research,
                           'force_bet': force_bet, 'feedback': feedback}, ensure_ascii=False)
        content, _ = await self.llm.complete(system, user, schema=self._decision_schema(observation, force_bet))
        draft = DecisionDraft.model_validate_json(content)
        return AgentDecision(round_id=observation.round_id, basis_event=observation.event_id,
                             **draft.model_dump())

    async def summarize_round(self, observation: Observation, feedback: str = '') -> AgentDecision:
        """공개 결과로 잠정 판단을 정정하고 중기·장기 기억을 갱신한다. 행동은 제출하지 않는다."""
        if observation.phase != 'revealed' or observation.result is None:
            raise ValueError('round summary requires a revealed result')
        system = (
            '끝난 인디언 포커 라운드를 회고해 개인 메모를 JSON으로 반환한다. '
            'round=이번 라운드의 판단과 실제 결과, game=이번 게임에서 다음에 취할 운영 방향, '
            'long=근거를 남긴 상대별 대응이다. 기존 개인 메모도 참고해 중요한 내용을 보존한다. '
            '한 판의 결과로 상대 성향을 확정하지 말고 관찰과 추측을 구분한다. '
            '공개되지 않은 폴드 카드는 알 수 없다. 새로운 발언이나 베팅은 하지 않는다. '
            '각 메모를 짧은 한두 문장으로 작성하고 바꿀 내용이 없으면 빈 문자열로 둔다.')
        user = json.dumps({'observation': observation.model_dump(), 'feedback': feedback}, ensure_ascii=False)
        content, _ = await self.llm.complete(system, user, schema=self._memory_schema())
        updates = MemoryUpdate.model_validate_json(content)
        return AgentDecision(round_id=observation.round_id, basis_event=observation.event_id,
                             speech='unavailable', memory_updates=updates)

    async def remember(self, observation: Observation, decision: AgentDecision):
        """검증된 개인 메모만 저장. 빈 문자열은 기존 내용을 유지한다."""
        for scope, text in decision.memory_updates.model_dump().items():
            if not text:
                continue
            if self.table.round is None or self.table.round.number != observation.round_id:
                raise ValueError('round changed before saving memories')
            if len(text) > self.config.memo_limit(scope):
                raise ValueError(f'{scope} memo is too long')
            await self.table.call_player_tool(self.mcp, self.player_id, 'write_memo',
                                              {'scope': scope, 'text': text})
