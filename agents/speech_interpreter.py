"""인간의 공개 발언을 한 번 해석한다. 개인 메모/카드/MCP 접근은 없다."""
import asyncio
import json
import re

from agent.llm_controller import create_llm
from .models import SpeechInterpretation
from .prompts import render_prompt


class SpeechInterpreter:
    def __init__(self, config, client=None):
        self.config = config
        self.client = client if client is not None else create_llm(config, role='router')

    async def interpret(self, text: str, ai_ids: list[str], target: str | None = None) -> SpeechInterpretation:
        system = render_prompt('speech_interpreter')
        schema = SpeechInterpretation.model_json_schema()
        # 구절 복사까지 작은 모델에 맡기지 않는다. 분류와 대상 해석만 모델이 맡는다.
        fragments = [part.strip() for part in re.split(
            r'(?<=[?!.。？！,])\s+|\n+|\s+(?=(?:그리고|그런데|근데|다만)\s)', text) if part.strip()]
        schema['$defs']['SpeechIntent']['properties']['text']['enum'] = fragments
        schema['$defs']['SpeechIntent']['properties']['targets']['items']['enum'] = ai_ids
        schema['$defs']['SpeechIntent']['properties']['targets']['minItems'] = 1
        schema['$defs']['SpeechIntent']['properties']['text']['description'] = '원문 속 연속 구절을 문장부호까지 그대로 복사한다.'
        user = {'text': text, 'source_fragments': fragments, 'ai_ids': ai_ids, 'explicit_target': target}
        for attempt in range(self.config.router_max_attempts):
            content, _ = await asyncio.wait_for(asyncio.to_thread(
                self.client.complete, system, json.dumps(user, ensure_ascii=False), schema=schema),
                self.config.router_timeout)
            try:
                return SpeechInterpretation.model_validate_json(content).validate_source(text, set(ai_ids))
            except ValueError as error:
                if attempt + 1 == self.config.router_max_attempts:
                    raise
                user['feedback'] = render_prompt('speech_retry', error=str(error))
