"""실행용 업무 계층. 연습은 poker_llm_practice.py에서 한다.

PlayerLLM은 HTTP/메모 첨부, 이 클래스는 게임 판단·검색·개인 메모 갱신을 담당한다.
"""
import json
from copy import deepcopy
from typing import Literal
from pydantic import Field

from .chunker import game_speech_text
from .models import AgentDecision, Bet, Contract, MemoryUpdate, Observation
from .prompts import render_prompt


class DecisionDraft(Contract):
    """공개 행동과 개인 메모를 구분한다. 출처·사건 ID는 모델이 만들지 않는다."""
    speech: Literal['speak', 'silent', 'unavailable']
    text: str
    bet: Bet | None
    wait_for_dialogue: bool
    response_channel: Literal['game', 'chat', 'mixed'] = 'game'
    memory_updates: MemoryUpdate = Field(default_factory=MemoryUpdate)


class PokerLLM:
    def __init__(self, table, mcp, player_id: str, llm):
        self.table = table
        self.mcp = mcp
        self.player_id = player_id
        self.llm = llm
        self.config = table.config
        self._research_cache = {}

    @staticmethod
    def _model_observation(observation: Observation) -> dict:
        payload = observation.model_dump()
        # 입력 방식은 엔진/라우터만 사용한다. 모델에게는 모두 동등한 참가자로 보낸다.
        payload['players'] = [{key: value for key, value in player.items() if key != 'kind'}
                              for player in payload['players']]
        return payload

    def route(self, observation: Observation, event: dict) -> str:
        """공개 후 회고 / 검증된 인간 잡담 / 게임 판단의 세 경로."""
        if observation.phase == 'revealed':
            return 'summary'
        sender = next((p for p in observation.players if p['id'] == event.get('player_id')), {})
        interpretation = event.get('interpretation')
        if sender.get('kind') == 'human' and interpretation:
            addressed = any(intent['kind'] != 'game' and self.player_id in intent['targets']
                            for intent in interpretation['intents'])
            if addressed:
                return 'mixed' if any(i['kind'] == 'game' for i in interpretation['intents']) else 'chat'
        is_chat = (event.get('type') == 'Speech' and event.get('channel') == 'chat'
                   and event.get('target') == self.player_id and sender.get('kind') == 'human')
        return 'chat' if is_chat else 'game'

    async def research(self, observation: Observation, event: dict) -> dict:
        """주장 신뢰성과 베팅 성향을 각각 검색. 유형별 ready 결과만 재사용한다."""
        candidates = [message for message in observation.messages
                      if message['player_id'] != self.player_id and game_speech_text(message).strip()]
        if event.get('type') == 'Speech' and event.get('player_id') != self.player_id and game_speech_text(event).strip():
            message = event
            opponent_id = message['player_id']
        elif event.get('type') == 'BetPlaced' and event.get('player_id') != self.player_id:
            opponent_id = event['player_id']
            message = next((m for m in reversed(candidates) if m['player_id'] == opponent_id), None)
        elif candidates:
            message = candidates[-1]
            opponent_id = message['player_id']
        else:
            return {'status': 'skipped', 'matches': []}
        queries = {}
        if message is not None:
            queries['claims'] = (f"상대 {opponent_id}의 과거 카드 주장과 블러핑: {game_speech_text(message)}",
                                 self.config.retrieval_claim_limit)
        call_cost = max(0, observation.current_bet - observation.contributions.get(self.player_id, 0))
        queries['betting'] = (
            f"상대 {opponent_id}의 과거 체크 콜 레이즈 폴드와 베팅 크기. "
            f"현재 테이블 최고 베팅 {observation.current_bet}, "
            f"상대 누적 베팅 {observation.contributions.get(opponent_id, 0)}, 내 콜 비용 {call_cost}",
            self.config.retrieval_betting_limit)
        searches, matches = {}, []
        for kind, (query, limit) in queries.items():
            query = query[:self.config.max_message_chars]
            key = (observation.round_id, opponent_id, query, limit)
            cached = self._research_cache.get(kind)
            if cached is not None and cached[0] == key:
                result = cached[1]
            else:
                result = await self.table.call_player_tool(self.mcp, self.player_id,
                    'search_experiences', {'query': query, 'limit': limit,
                                          'opponent_id': opponent_id, 'search_kind': kind})
                if result.get('status') == 'ready':
                    self._research_cache[kind] = (key, deepcopy(result))
            # 본문은 matches에 한 번만 담고, 각 검색의 상태/출처는 별도로 남긴다.
            searches[kind] = {key: value for key, value in result.items() if key != 'matches'}
            searches[kind].update(opponent_id=opponent_id, query=query)
            matches.extend({**deepcopy(match), 'search_kind': kind} for match in result.get('matches', []))
        statuses = {search['status'] for search in searches.values()}
        status = 'ready' if statuses == {'ready'} else 'unavailable' if 'unavailable' in statuses else 'indexing'
        return {'status': status, 'searches': searches, 'matches': matches}

    async def chat(self, observation: Observation, event: dict, feedback: str = '') -> AgentDecision:
        system = render_prompt('poker_chat')
        user = json.dumps({'question': event.get('text', ''), 'interpretation': event.get('interpretation'), 'player_id': self.player_id, 'feedback': feedback}, ensure_ascii=False)
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

    def _decision_schema(self, observation: Observation, force_bet: bool, allow_chat: bool = False) -> dict:
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
        if allow_chat:
            channels = (['mixed'] if observation.required_game_utterances > 0 and observation.can_speak
                        else ['chat', 'mixed'] if observation.can_speak else ['chat'])
        else:
            channels = ['game']
        for channel in channels:
            for speech in (['speak'] if allow_chat else speeches):
                for has_bet in ([False] if channel == 'chat' else bets):
                    variant = deepcopy(schema)
                    properties = variant['properties']
                    variant['required'] = list(variant['required'])
                    if allow_chat:
                        variant['required'].append('response_channel')
                    properties['response_channel']['enum'] = [channel if speech == 'speak' else 'game']
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
                     force_bet: bool = False, feedback: str = '', *, human_event: dict | None = None) -> AgentDecision:
        system = render_prompt('poker_decision', action_selection=render_prompt('poker_action_selection'))
        if human_event:
            system += '\n\n' + render_prompt('poker_mixed', player_id=self.player_id, speaker_id=human_event['player_id'])
        user = json.dumps({'observation': self._model_observation(observation), 'addressed_request': human_event, 'past_experiences': research,
                           'force_bet': force_bet, 'feedback': feedback}, ensure_ascii=False)
        content, _ = await self.llm.complete(system, user, schema=self._decision_schema(observation, force_bet, allow_chat=human_event is not None))
        draft = DecisionDraft.model_validate_json(content)
        payload = draft.model_dump(exclude={'response_channel'})
        if human_event and draft.speech == 'speak':
            payload.update(channel=draft.response_channel, target=human_event['player_id'],
                           reply_to=human_event['request_id'])
        return AgentDecision(round_id=observation.round_id, basis_event=observation.event_id, **payload)

    async def mixed(self, observation: Observation, event: dict, research: dict,
                    force_bet: bool = False, feedback: str = '') -> AgentDecision:
        return await self.decide(observation, research, force_bet, feedback, human_event=event)

    async def summarize_round(self, observation: Observation, feedback: str = '') -> AgentDecision:
        """공개 결과로 잠정 판단을 정정하고 중기·장기 기억을 갱신한다. 행동은 제출하지 않는다."""
        if observation.phase != 'revealed' or observation.result is None:
            raise ValueError('round summary requires a revealed result')
        system = render_prompt('poker_summary')
        user = json.dumps({'observation': self._model_observation(observation), 'feedback': feedback}, ensure_ascii=False)
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
