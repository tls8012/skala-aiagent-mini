"""고정 배패 10개, 고정 과거 뱅크로 A–B / A–C 라운드 성과 비교."""
from .deals import FIXED_DEALS, deal_cards
from .fixtures import Scenario
from .opponents import FixedPolicy

HISTORY_SEED = 30042


def scenarios(count, seed):
    if not 1 <= count <= len(FIXED_DEALS):
        raise ValueError('stage3 cases must be 1..10 distinct deals')
    return [Scenario(f's3-{pid}-{i+1:02d}', 'FIXED_DEAL', seed, pid, i)
            for pid in ('B', 'C') for i in range(count)]


async def run(recorder):
    from .execution import run_cases
    cases = scenarios(recorder.config.cases, recorder.config.seed)
    recorder.manifest['protocol'] = {
        'name': 'fixed_deal_heads_up', 'version': 2, 'unit': 'completed_round_per_deal_opponent_condition',
        'deals_per_opponent': recorder.config.cases, 'history_seed': HISTORY_SEED,
        'history_rounds_per_opponent': 20, 'history_ids': 'deterministic',
        'history_policy_version': FixedPolicy.version,
        'retrieval': 'smoke_fixture' if recorder.config.backend == 'fake' else 'chroma',
        'memo_reset_between_trials': True, 'evaluates_win_rate': True,
        'deals': [{'case_id': c.case_id, 'cards': {p: card.to_dict() for p, card in deal_cards(c).items()}}
                  for c in cases],
    }
    recorder.save_manifest()
    await run_cases(recorder, cases)
