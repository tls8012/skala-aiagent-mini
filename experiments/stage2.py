"""검색/메모 네 조건의 고정 판세 비교."""
from .execution import run_cases
from .fixtures import scenarios


async def run(recorder):
    await run_cases(recorder, scenarios(2, recorder.config.cases, recorder.config.seed))
