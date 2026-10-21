"""OpenAI Responses API 어댑터. 게임/메모 로직 없이 기존 complete/stream 계약을 제공한다."""
from copy import deepcopy
import json
import os
from urllib.parse import quote

import httpx

OPENAI_URL = 'https://api.openai.com/v1/responses'


def structured_format(schema: dict) -> dict:
    """OpenAI의 object root 제약을 전송 계층에서 처리한다. 원본 스키마는 변경하지 않는다."""
    def normalize(node):
        result = deepcopy(node)
        result.pop('default', None)
        if 'const' in result:
            result['enum'] = [result.pop('const')]
        for key in ('properties', '$defs'):
            if key in result:
                result[key] = {name: normalize(child) for name, child in result[key].items()}
        for key in ('anyOf', 'allOf', 'oneOf'):
            if key in result:
                result[key] = [normalize(child) for child in result[key]]
        if isinstance(result.get('items'), dict):
            result['items'] = normalize(result['items'])
        if result.get('type') == 'object' or 'properties' in result:
            result['required'] = list(result.get('properties', {}))
            result['additionalProperties'] = False
        ref = result.get('$ref', '')
        if ref.startswith('#') and not ref.startswith('#/$defs/'):
            result['$ref'] = '#/properties/result' + ref[1:]
        return result

    body = normalize(schema)
    definitions = body.pop('$defs', {})
    wrapped = {'type': 'object', 'properties': {'result': body},
               'required': ['result'], 'additionalProperties': False}
    if definitions:
        wrapped['$defs'] = definitions
    return {'type': 'json_schema', 'name': 'agent_result', 'strict': True, 'schema': wrapped}


class OpenAILLM:
    def __init__(self, model: str, *, max_output_tokens=1024, reasoning_effort='none',
                 timeout=120.0, usd_per_1k_tokens=None, api_key=None):
        self.name, self.model = f'openai:{model}', model
        self.max_output_tokens, self.reasoning_effort = max_output_tokens, reasoning_effort
        self.timeout, self.usd_per_1k = timeout, usd_per_1k_tokens
        self._api_key = api_key

    def _headers(self):
        key = self._api_key if self._api_key is not None else os.environ.get('OPENAI_API_KEY', '')
        if not key.strip():
            raise RuntimeError('OPENAI_API_KEY가 설정되지 않았습니다. 환경변수 또는 .env를 확인하세요.')
        return {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}

    def model_info(self):
        response = httpx.get('https://api.openai.com/v1/models/' + quote(self.model, safe=''),
                             headers=self._headers(), timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def _payload(self, system, user, *, stream=False, schema=None):
        payload = {'model': self.model, 'store': False, 'stream': stream,
                   'input': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                   'max_output_tokens': self.max_output_tokens}
        if self.reasoning_effort is not None:
            payload['reasoning'] = {'effort': self.reasoning_effort}
        if self.reasoning_effort == 'none':
            payload['temperature'] = 0.0
        if schema is not None:
            payload['text'] = {'format': structured_format(schema)}
        return payload

    def _usage(self, data):
        usage = data.get('usage') or {}
        tokens = usage.get('total_tokens', (usage.get('input_tokens') or 0) + (usage.get('output_tokens') or 0))
        return {'model': self.name, 'actual_model': data.get('model', self.model), 'tokens': tokens,
                'input_tokens': usage.get('input_tokens', 0), 'output_tokens': usage.get('output_tokens', 0),
                # 선택적 비교용 블렌디드 단가. 실제 OpenAI 청구 금액으로 오인하지 않는다.
                'usd': round(tokens/1000*self.usd_per_1k, 6) if self.usd_per_1k is not None else None}

    @staticmethod
    def _text(data, *, schema=None):
        if data.get('status') != 'completed':
            detail = (data.get('incomplete_details') or {}).get('reason')
            raise RuntimeError(f'OpenAI response {data.get("status")}: {detail or "not completed"}')
        pieces = []
        for item in data.get('output', []):
            if item.get('type') != 'message' or item.get('phase') not in (None, 'final_answer'):
                continue
            for content in item.get('content', []):
                if content.get('type') == 'refusal':
                    raise RuntimeError('OpenAI refused this response')
                if content.get('type') == 'output_text':
                    pieces.append(content['text'])
        text = ''.join(pieces)
        if not text.strip():
            raise RuntimeError('OpenAI returned no final text')
        if schema is not None:
            wrapped = json.loads(text)
            if not isinstance(wrapped, dict) or set(wrapped) != {'result'}:
                raise ValueError('OpenAI structured response must contain only result')
            return json.dumps(wrapped['result'], ensure_ascii=False)
        return text

    def complete(self, system, user, schema=None):
        response = httpx.post(OPENAI_URL, headers=self._headers(),
                              json=self._payload(system, user, schema=schema), timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        return self._text(data, schema=schema), self._usage(data)

    async def stream(self, system, user, on_token=None):
        completed = None
        phases = {}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream('POST', OPENAI_URL, headers=self._headers(),
                                     json=self._payload(system, user, stream=True)) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith('data:') or line[5:].strip() == '[DONE]':
                        continue
                    event = json.loads(line[5:].strip())
                    kind = event.get('type')
                    if kind == 'response.output_item.added':
                        phases[event['output_index']] = event['item'].get('phase')
                    elif kind == 'response.output_text.delta' and on_token is not None and phases.get(event.get('output_index')) in (None, 'final_answer'):
                        on_token(event['delta'])
                    elif kind == 'response.completed':
                        completed = event['response']
                    elif kind in {'response.failed', 'response.incomplete', 'error', 'response.refusal.delta'}:
                        raise RuntimeError(f'OpenAI stream ended with {kind}')
        if completed is None:
            raise RuntimeError('OpenAI stream ended before response.completed')
        usage = self._usage(completed)
        usage['prompt'] = f'[SYSTEM]\n{system}\n\n[USER]\n{user}'
        return self._text(completed), usage
