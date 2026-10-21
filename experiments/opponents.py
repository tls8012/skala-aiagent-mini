"""모델/검색/개인 메모가 없는 관찰 기반 정책. Table은 주입하지 않는다."""
import random

from agents.models import AgentDecision, Bet, Observation
from .common import digest


class FixedPolicy:
    version = 'fixed-v1'

    def __init__(self, player_id, style, seed, case_id):
        self.player_id, self.style, self.seed, self.case_id = player_id, style, seed, case_id
        self.counts = {}
        self.raises = {}
        self.cache = {}
        self.seen_events = set()

    def on_event(self, event):
        key = (event['round_id'], event['event_id'])
        if key in self.seen_events:
            return
        self.seen_events.add(key)
        if event['type'] == 'BetPlaced' and event['player_id'] == self.player_id and event['action'] == 'raise':
            number = event['round_id']
            self.raises[number] = self.raises.get(number, 0) + 1

    def decide(self, observation):
        key = (observation.round_id, observation.event_id)
        if key in self.cache:
            return self.cache[key]
        number = observation.round_id
        speech, text, bet = 'unavailable', '', None
        if observation.phase != 'revealed':
            if observation.can_speak and observation.required_game_utterances:
                speech = 'speak'
                visible = observation.visible_cards.get('A', {})
                text = ('네 카드는 10 이하야.' if self.style == 'aggressive' or visible.get('rank', 2) <= 10
                        else '네 카드는 J 이상이야.') if self.style != 'reference' else '상대의 행동을 보고 결정하겠어.'
            elif observation.required_game_utterances == 0:
                speech = 'silent'
            if observation.allowed_bets and not observation.required_game_utterances:
                ordinal = self.counts.get(number, 0)
                self.counts[number] = ordinal + 1
                u = random.Random(int(digest([self.seed, self.case_id, number, ordinal])[:16], 16)).random()
                free = 'check' in observation.allowed_bets
                action, size = ('check' if free else 'call'), None
                if self.style == 'aggressive':
                    if u < (0.7 if free else 0.5) and self.raises.get(number, 0) < 2:
                        action = 'raise'
                        size = next((s for s in ('large', 'medium', 'small') if s in observation.raise_options), None)
                    elif not free and u >= 0.9:
                        action = 'fold'
                elif self.style == 'defensive':
                    if free and u < 0.1 and self.raises.get(number, 0) < 1:
                        action, size = 'raise', 'small' if 'small' in observation.raise_options else None
                    elif not free:
                        rank = observation.visible_cards.get('A', {}).get('rank', 2)
                        action = 'call' if u < (0.2 if rank >= 11 else 0.6) else 'fold'
                if action == 'raise' and ('raise' not in observation.allowed_bets or size is None):
                    action, size = ('check' if free else 'call'), None
                bet = Bet(action=action, raise_size=size)
        result = AgentDecision(round_id=observation.round_id, basis_event=observation.event_id,
                               speech=speech, text=text, bet=bet)
        self.cache[key] = result
        return result

    async def ainvoke(self, state):
        return {'decision': self.decide(Observation.model_validate(state['observation'])).model_dump()}
