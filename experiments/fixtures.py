"""고정 상황과 과거 자료를 기존 Table/명령/정산으로 생성한다."""
from copy import deepcopy
from dataclasses import dataclass, replace
import json
import random
from uuid import uuid4

from agents.cards import Card, Deck
from agents.models import Bet, PlayerCommand
from agents.table import Table
from .common import digest, ObservedTable
from .opponents import FixedPolicy

FAMILIES = ('BLUFF', 'TRUTH', 'AGGRESSION', 'CONSERVATIVE', 'LIMITED')


@dataclass(frozen=True)
class Scenario:
    case_id: str
    family: str
    seed: int
    opponent: str
    index: int


def scenarios(stage, count, seed, opponent=None):
    return [Scenario(f's{stage}-{opponent or FAMILIES[i % 5]}-{(i+1 if opponent else i//5+1):02d}',
                     FAMILIES[i % 5], seed + stage*10000 + i, opponent or ('C' if i % 5 == 3 else 'B'), i)
            for i in range(count)]


def command(table, pid, kind, **extra):
    result = table.submit(pid, PlayerCommand(request_id=uuid4().hex, round_id=table.round.number,
                           basis_event=table.event_id, kind=kind, **extra))
    if result.status != 'accepted':
        raise ValueError(f'fixture command failed: {result}')
    return result


def build_table(config, case, game_id, *, stage, recorder=None, trial_id=None):
    # 3단계에서는 30개 기준 15/15 선행. 카드의 사람별 배정은 순서와 무관하다.
    order = (['A', case.opponent] if case.index % 2 == 0 else [case.opponent, 'A']) if stage == 3 else [case.opponent, 'A']
    table = (ObservedTable(order, config=config, game_id=game_id, recorder=recorder, trial_id=trial_id)
             if recorder else Table(order, config=config, game_id=game_id))
    cards = Deck(random.Random(case.seed)).deal(2)
    table.start_round({'A': cards[0], case.opponent: cards[1]})
    if stage != 3:
        command(table, 'A', 'speak', text='이번 주장을 듣고 판단하겠어.')
        claim = '네 카드는 10 이하야.' if case.opponent == 'B' else '네 카드는 J 이상이야.'
        command(table, case.opponent, 'speak', text=claim)
        command(table, case.opponent, 'bet', bet=Bet(action='raise', raise_size=('small', 'medium', 'large')[case.index % 3]))
        observation = table.observe('A')
        if not observation.can_speak or observation.required_game_utterances or observation.betting_turn != 'A':
            raise ValueError('invalid prepared decision point')
    return table


def history_bank(config, case, *, stage):
    """과거는 별도 seed의 완료 라운드. 공개 export의 라운드 ID만 데이터셋 순번으로 정규화한다."""
    rows, provenance = [], []
    for i in range(20):
        pid = case.opponent
        if stage != 3 and case.family == 'LIMITED' and i > 0:
            pid = 'D'
        game_id = f'history-{i+1:02d}'
        historical = replace(config, data_dir=config.data_dir / 'history-build', seed=case.seed+100000+i)
        table = Table(['A', pid], config=historical, game_id=game_id)
        rng = random.Random(case.seed+100000+i)
        cards = Deck(rng).deal(2)
        # BLUFF/TRUTH는 진위 방향을 의도적으로 바꾼 통제된 과거 자료다. 3단계는 실제 정책 자료만 쓴다.
        if stage != 3 and case.family in {'BLUFF', 'TRUTH'}:
            high = (i < 16) if case.family == 'BLUFF' else (i >= 16)
            cards = [Card(rng.choice(range(11, 15) if high else range(2, 11)), 'S'), Card(7, 'H')]
        table.start_round({'A': cards[0], pid: cards[1]})
        style = 'defensive' if pid == 'C' else 'aggressive'
        policy = FixedPolicy(pid, style, case.seed+100000, f'bank-{i}')
        reference = FixedPolicy('A', 'reference', 0, f'bank-{i}')
        command(table, 'A', 'speak', text='일단 공개 결과까지 보자.')
        first = policy.decide(table.observe(pid))
        command(table, pid, 'speak', text=first.text)
        while table.round.phase == 'dialogue':
            actor = table.round.betting.turn
            decision = (reference if actor == 'A' else policy).decide(table.observe(actor))
            before = table.event_id
            command(table, actor, 'bet', bet=decision.bet)
            for event in table.events_since(before):
                policy.on_event(event)
        record = json.loads(next(table.archive_dir.glob('round_*.json')).read_text())
        provenance.append({'dataset_round': i+1, 'original_game_id': game_id, 'original_round_id': 1,
                           'original_sha256': digest(record), 'policy': style, 'policy_version': policy.version})
        record['round_id'] = i+1
        for event in record['events']:
            event['round_id'] = i+1
        rows.append(record)
    return rows, provenance


def memoir(rows, opponent):
    claims = []
    raises = folds = 0
    for row in rows:
        for event in row['events']:
            if event.get('player_id') == opponent:
                if event['type'] == 'Speech' and '10 이하' in event.get('text', ''):
                    revealed = row['result']['revealed_cards'].get('A')
                    if revealed:
                        claims.append(revealed['rank'] <= 10)
                if event['type'] == 'BetPlaced':
                    raises += event['action'] == 'raise'
                    folds += event['action'] == 'fold'
    note = (f'공개 과거 자료에서 {opponent}의 “A 카드는 10 이하” 주장 {len(claims)}건 중 '
            f'{sum(claims)}건 일치, {len(claims)-sum(claims)}건 불일치를 관찰했다. '
            f'레이즈 {raises}회, 폴드 {folds}회였다. 이번 주장도 현재 판세와 함께 판단해야 하며 확정 성향은 아니다.')
    return {'round': '', 'game': '콜 비용과 상대 주장 신뢰도를 함께 확인한다.', 'long': note}


def fixed_evidence(rows, opponent, unrelated=False):
    candidates = [r for r in rows if any(e.get('player_id') == opponent for e in r['events'])]
    if unrelated:
        # 별도 참가자들에 대한 공개 사실로 바꾼 자료를 선택하는 대신 호출자가 만든 별도 뱅크를 받는다.
        candidates = rows
    result = []
    for row in candidates[:3]:
        events = [e for e in row['events'] if e['type'] in {'Speech', 'BetPlaced', 'RoundEnded'}]
        text = json.dumps({'events': events, 'result': row['result']}, ensure_ascii=False)
        result.append({'id': f"fixture-{row['round_id']}", 'text': text, 'distance': 0.0,
                       'metadata': {'experience_id': f"played-{row['round_id']}", 'round_id': row['round_id'],
                                    'source': 'played', 'event_type': 'fixture_public_bundle'}})
    return {'status': 'ready', 'matches': result}


def input_hash(table):
    observation = deepcopy(table.observe('A').model_dump())
    observation.pop('game_id')
    # UUID는 추적 값이다. 문장/차례/금액/카드/사건 번호는 유지한다.
    for message in observation['messages']:
        message.pop('request_id', None)
    return digest(observation)
