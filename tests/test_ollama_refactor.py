"""업무/통신 경계의 회귀 검사. Ollama 서버나 외부 API 없이 실행한다."""
import json
import os
import unittest
from unittest.mock import patch

import httpx

os.environ['LANGCHAIN_TRACING_V2'] = 'false'
os.environ['LANGSMITH_TRACING'] = 'false'

from agent.incident_llm import IncidentFastLLM, IncidentQualityLLM
from agent.ollama_client import OllamaLLM
from agent.ollama_llm import OllamaFastLLM, OllamaQualityLLM


class StubClient:
    name = 'stub'

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def complete(self, system, user, schema=None):
        self.calls.append((system, user, schema))
        return self.answer, {'usd': 0.0}

    async def stream(self, system, user, on_token=None):
        self.calls.append((system, user, None))
        if on_token:
            on_token(self.answer)
        return self.answer, {'usd': 0.0, 'prompt': user}


class IncidentContractTests(unittest.IsolatedAsyncioTestCase):
    def test_invalid_route_and_missing_research_are_recovered(self):
        client = StubClient('invalid JSON')
        llm = IncidentFastLLM(client)
        self.assertEqual(llm.route('장애 진단')[0]['route'], 'incident')
        plan, _ = llm.plan('auth-service와 web-01 비교', '표준 절차')
        self.assertEqual([t['target'] for t in plan['tasks']], ['auth-service', 'web-01'])
        self.assertTrue(all(t['status'] == 'pending' for t in plan['tasks']))

    def test_plan_normalizes_targets_and_deduplicates(self):
        client = StubClient(json.dumps({'needs_analysis': True, 'tasks': [
            {'agent': 'research', 'target': 'typo'},
            {'agent': 'research', 'target': 'auth-service'},
            {'agent': 'analysis'}, {'agent': 'analysis'}]}))
        plan, _ = IncidentFastLLM(client).plan('auth-service 장애', '절차')
        self.assertEqual(len(plan['tasks']), 2)
        self.assertEqual(plan['tasks'][0]['target'], 'auth-service')
        self.assertTrue(plan['needs_analysis'])

    async def test_history_feedback_and_token_callback(self):
        client = StubClient('답변')
        tokens = []
        text, _ = await IncidentFastLLM(client).chat(
            '이름은?', [{'role': 'user', 'content': '나는 민수'}], tokens.append)
        self.assertEqual(text, '답변')
        self.assertIn('나는 민수', client.calls[-1][1])
        await IncidentQualityLLM(client).write(
            '장애', [{'source': 'runbook', 'data': '재시작 절차'}], 2,
            '번호를 넣으세요', tokens.append)
        self.assertIn('재시작 절차', client.calls[-1][1])
        self.assertIn('번호를 넣으세요', client.calls[-1][1])
        self.assertEqual(tokens, ['답변', '답변'])

    def test_existing_constructors(self):
        for cls in (OllamaFastLLM, OllamaQualityLLM):
            self.assertEqual(cls('test-model', 0.005).name, 'ollama:test-model')


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def test_structured_request_usage_and_http_failure(self):
        llm = OllamaLLM('test', 0.005)
        req = httpx.Request('POST', 'http://test')
        response = httpx.Response(200, request=req, json={
            'message': {'content': '{"ok":true}'},
            'prompt_eval_count': 10, 'eval_count': 5})
        with patch('agent.ollama_client.httpx.post', return_value=response) as post:
            content, usage = llm.complete('system', 'user', {'type': 'object'})
        self.assertEqual(json.loads(content), {'ok': True})
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['format'], {'type': 'object'})
        self.assertFalse(payload['stream'])
        self.assertEqual(usage, {'model': 'ollama:test', 'tokens': 15, 'usd': 0.000075})
        with patch('agent.ollama_client.httpx.post', return_value=httpx.Response(500, request=req)):
            with self.assertRaises(httpx.HTTPStatusError):
                llm.complete('system', 'user')

    async def test_ndjson_stream_and_final_usage(self):
        requests = []

        def handler(request):
            requests.append(json.loads(request.content))
            lines = [
                {'message': {'content': '안녕'}, 'done': False},
                {'message': {'content': '하세요'}, 'done': False},
                {'done': True, 'prompt_eval_count': 20, 'eval_count': 10},
            ]
            return httpx.Response(200, text='\n\n'.join(json.dumps(x) for x in lines))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        tokens = []
        with patch('agent.ollama_client.httpx.AsyncClient', return_value=client):
            text, usage = await OllamaLLM('test', 0.005).stream('system', 'user', tokens.append)
        self.assertEqual(text, '안녕하세요')
        self.assertEqual(tokens, ['안녕', '하세요'])
        self.assertEqual(usage['tokens'], 30)
        self.assertEqual(usage['usd'], 0.00015)
        self.assertIn('[SYSTEM]\nsystem', usage['prompt'])
        self.assertTrue(requests[0]['stream'])
        self.assertNotIn('format', requests[0])


if __name__ == '__main__':
    unittest.main()
