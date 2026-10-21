"""A–B / A–C 독립 heads-up 라운드. 웹/서버를 거치지 않는다."""
from .execution import run_cases
from .fixtures import scenarios


async def run(recorder):
    cases = [case for pid in ('B', 'C') for case in scenarios(3, recorder.config.cases, recorder.config.seed, pid)]
    await run_cases(recorder, cases)
