"""정보 주입: 같은 30개 상황의 세 조건 평가."""
from .execution import run_cases
from .fixtures import scenarios


async def run(recorder):
    await run_cases(recorder, scenarios(1, recorder.config.cases, recorder.config.seed))
