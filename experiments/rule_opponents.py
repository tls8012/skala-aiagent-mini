"""stage3의 결정적 상대. 관찰된 A 카드와 콜 비용·자신의 공개 레이즈만 사용한다."""
from agents.models import AgentDecision, Bet, Observation


class RuleOpponent:
    def __init__(self, player_id):
        self.player_id = player_id
        self.raises = {}
        self.seen_events = set()
        self.cache = {}

    def on_event(self, event):
        key = (event['round_id'], event['event_id'])
        if key in self.seen_events:
            return
        self.seen_events.add(key)
        if (event['type'] == 'BetPlaced' and event['player_id'] == self.player_id
                and event['action'] == 'raise'):
            number = event['round_id']
            self.raises[number] = self.raises.get(number, 0) + 1

    @staticmethod
    def rank(observation):
        return observation.visible_cards['A']['rank']

    def cost(self, observation):
        return observation.current_bet - observation.contributions[self.player_id]

    @staticmethod
    def passive(observation):
        return Bet(action='check' if 'check' in observation.allowed_bets else 'call')

    def raise_or_pass(self, observation, sizes):
        if 'raise' in observation.allowed_bets:
            for size in sizes:
                if size in observation.raise_options:
                    return Bet(action='raise', raise_size=size)
        return self.passive(observation)

    def decide(self, observation):
        key = (observation.round_id, observation.event_id)
        if key in self.cache:
            return self.cache[key]
        speech, text, bet = 'unavailable', '', None
        if observation.phase != 'revealed':
            if observation.can_speak and observation.required_game_utterances:
                speech, text = 'speak', self.claim(observation)
            elif observation.required_game_utterances == 0:
                speech = 'silent'
            if observation.allowed_bets and not observation.required_game_utterances:
                bet = self.choose_bet(observation)
        decision = AgentDecision(round_id=observation.round_id, basis_event=observation.event_id,
                                 speech=speech, text=text, bet=bet)
        self.cache[key] = decision
        return decision

    async def ainvoke(self, state):
        return {'decision': self.decide(Observation.model_validate(state['observation'])).model_dump()}


class BRulePolicy(RuleOpponent):
    """범위를 반대로 말하고 낮은 상대 패를 압박하는 블러퍼. 레이즈는 라운드당 한 번."""
    version = 'rules-v2-b'
    style = 'rule_bluffer'

    def __init__(self, player_id='B'):
        super().__init__(player_id)

    def claim(self, observation):
        return '네 카드는 J 이상이야.' if self.rank(observation) <= 10 else '네 카드는 10 이하야.'

    def choose_bet(self, observation):
        rank, cost = self.rank(observation), self.cost(observation)
        raised = self.raises.get(observation.round_id, 0) > 0
        if rank <= 7:
            return self.passive(observation) if raised else self.raise_or_pass(observation, ('large', 'medium', 'small'))
        if rank <= 10:
            if cost == 0:
                return self.passive(observation) if raised else self.raise_or_pass(observation, ('medium', 'small'))
            return Bet(action='call' if cost <= 4 else 'fold')
        return self.passive(observation) if cost <= 1 else Bet(action='fold')


class CRulePolicy(RuleOpponent):
    """범위를 사실대로 말하며 낮은 상대 패에는 콜을 유지하는 수비형."""
    version = 'rules-v2-c'
    style = 'rule_defender'

    def __init__(self, player_id='C'):
        super().__init__(player_id)

    def claim(self, observation):
        return '네 카드는 10 이하야.' if self.rank(observation) <= 10 else '네 카드는 J 이상이야.'

    def choose_bet(self, observation):
        rank, cost = self.rank(observation), self.cost(observation)
        if cost == 0:
            if rank <= 7 and not self.raises.get(observation.round_id, 0):
                return self.raise_or_pass(observation, ('small',))
            return Bet(action='check')
        return Bet(action='call' if rank <= 7 or rank <= 10 and cost <= 2 else 'fold')


class ReferencePolicy(RuleOpponent):
    """과거 자료 생성용 A. 체크/콜만 하여 새 상대의 공개 결과를 확보한다."""
    version = 'rules-v2-reference'
    style = 'reference'

    def claim(self, observation):
        return '상대의 행동을 보고 결정하겠어.'

    def choose_bet(self, observation):
        return self.passive(observation)


def make_opponent(player_id):
    return {'B': BRulePolicy, 'C': CRulePolicy}[player_id]()


POLICY_VERSIONS = {'B': BRulePolicy.version, 'C': CRulePolicy.version}
