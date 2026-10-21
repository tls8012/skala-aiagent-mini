"""공급자 선택, OpenAI JSON 스키마/오류/스트림과 기존 게임 연결 검사. API 호출 없음."""
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx

from agent.llm_controller import configure_model, create_llm, model_name
from agent.ollama_client import OllamaLLM
from agent.openai_client import OPENAI_URL, OpenAILLM, structured_format
from agents.config import GameConfig, export_mcp_config, load_mcp_config
from agents.models import Bet, PlayerCommand
from agents.player_llm import PlayerLLM
from agents.poker_llm import PokerLLM
from agents.speech_interpreter import SpeechInterpreter
from agents.table import Table
from server import create_app


def response(text, **extra):
    return {'status': 'completed', 'model': 'gpt-6-luna',
            'output': [{'type': 'reasoning', 'summary': []},
                       {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': text}]}],
            'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}, **extra}


class ControllerTests(unittest.TestCase):
    def test_factory_retains_ollama_settings_and_selects_openai_roles(self):
        config = GameConfig()
        self.assertIsInstance(create_llm(config), OllamaLLM)
        self.assertEqual(create_llm(config, 'router').model, config.router_model)
        config = replace(config, llm_backend='openai')
        self.assertEqual(create_llm(config).model, 'gpt-6-luna')
        self.assertEqual(create_llm(config, 'router').model, 'gpt-6-luna')
        config = replace(config, openai_model='player-model', openai_router_model='router-model')
        self.assertEqual(model_name(config), 'player-model')
        self.assertEqual(create_llm(config, 'router').model, 'router-model')
        self.assertEqual(create_llm(config, 'router').max_output_tokens, config.openai_router_max_output_tokens)
        changed = configure_model(config, 'another-player')
        self.assertEqual(changed.openai_model, 'another-player')
        self.assertEqual(changed.ollama_model, config.ollama_model)

    def test_config_roundtrip_contains_no_api_key(self):
        config = GameConfig(llm_backend='openai', openai_model='custom')
        with patch.dict(os.environ, {**export_mcp_config(config), 'OPENAI_API_KEY': 'secret-for-test'}):
            self.assertEqual(load_mcp_config(), config)
            self.assertNotIn('secret-for-test', json.dumps(export_mcp_config(config)))

    def test_player_and_human_interpreter_share_controller(self):
        with TemporaryDirectory() as folder:
            config = GameConfig(data_dir=Path(folder), llm_backend='openai', openai_router_model='router-test')
            table = Table(['A','B'], config=config)
            self.assertEqual(PlayerLLM(table, object(), 'A').name, 'openai:gpt-6-luna')
            self.assertEqual(SpeechInterpreter(config).client.name, 'openai:router-test')

    def test_existing_poker_anyof_schema_is_wrapped_without_losing_bet_constraints(self):
        with TemporaryDirectory() as folder:
            table = Table(['A','B'], config=GameConfig(data_dir=Path(folder)))
            table.start_round()
            for pid in ('A','B'):
                table.submit(pid, PlayerCommand(request_id=pid, round_id=1, basis_event=table.event_id,
                                                kind='speak', text='참가'))
            schema = PokerLLM(table, None, 'A', None)._decision_schema(table.observe('A'), False)
            before = deepcopy(schema)
            wire = structured_format(schema)['schema']
            self.assertNotIn('anyOf', wire)
            self.assertIn('anyOf', wire['properties']['result'])
            self.assertEqual(wire['$defs']['Bet']['anyOf'][1]['properties']['action']['enum'], ['raise'])
            self.assertEqual(wire['required'], ['result'])
            self.assertEqual(schema, before)
            def check(node):
                if isinstance(node, dict):
                    self.assertNotIn('default', node)
                    if node.get('type') == 'object':
                        self.assertEqual(set(node['required']), set(node['properties']))
                        self.assertFalse(node['additionalProperties'])
                    for value in node.values():
                        check(value)
                elif isinstance(node, list):
                    for value in node:
                        check(value)
            check(wire)

    def test_openai_complete_unwraps_json_and_returns_usage(self):
        llm = OpenAILLM('gpt-6-luna', api_key='test-key')
        schema = {'type':'object', 'properties':{'ok':{'type':'boolean'}}, 'required':['ok']}
        data = response('{"result":{"ok":true}}')
        with patch('agent.openai_client.httpx.post', return_value=httpx.Response(200, json=data, request=httpx.Request('POST', OPENAI_URL))) as post:
            text, usage = llm.complete('system', 'user', schema)
        self.assertEqual(json.loads(text), {'ok':True})
        self.assertEqual(usage['tokens'], 15)
        self.assertIsNone(usage['usd'])
        request = post.call_args.kwargs
        self.assertEqual(request['headers']['Authorization'], 'Bearer test-key')
        self.assertFalse(request['json']['store'])
        self.assertEqual(request['json']['reasoning']['effort'], 'none')
        self.assertEqual(request['json']['temperature'], 0)

    def test_reasoning_models_do_not_receive_unsupported_temperature(self):
        llm = OpenAILLM('another-openai-model', reasoning_effort='low')
        self.assertNotIn('temperature', llm._payload('s','u'))
        self.assertNotIn('temperature', OpenAILLM('gpt-6-luna', reasoning_effort=None)._payload('s','u'))

    def test_session_backend_selection_also_applies_to_direct_table_users(self):
        from agents.game_service import GameSession
        with TemporaryDirectory() as folder:
            table = Table(['A','B'], config=GameConfig(data_dir=Path(folder)))
            session = GameSession(table, 'openai', mcp=object())
            self.assertEqual(table.config.llm_backend, 'openai')
            self.assertEqual(session.runner.interpreter.client.model, 'gpt-6-luna')

    def test_missing_key_and_incomplete_or_refused_output_do_not_become_actions(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY':''}):
            with self.assertRaisesRegex(RuntimeError, 'OPENAI_API_KEY'):
                OpenAILLM('gpt-6-luna').complete('s','u')
        data = response('{"result":{"ok":true}}', status='incomplete', incomplete_details={'reason':'max_output_tokens'})
        with self.assertRaisesRegex(RuntimeError, 'max_output_tokens'):
            OpenAILLM._text(data, schema={})
        data = response('')
        data['output'][1]['content'] = [{'type':'refusal', 'refusal':'refused'}]
        with self.assertRaisesRegex(RuntimeError, 'refused'):
            OpenAILLM._text(data)

    def test_http_errors_propagate_without_falling_back_to_ollama(self):
        raw = httpx.Response(401, request=httpx.Request('POST', OPENAI_URL), json={'error':'unauthorized'})
        with patch('agent.openai_client.httpx.post', return_value=raw):
            with self.assertRaises(httpx.HTTPStatusError):
                OpenAILLM('gpt-6-luna', api_key='invalid').complete('s','u')


class OpenAIIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_delivers_final_text_deltas_and_usage(self):
        events = [
            {'type':'response.output_item.added', 'output_index':0, 'item':{'phase':'commentary'}},
            {'type':'response.output_text.delta', 'output_index':0, 'delta':'ignored'},
            {'type':'response.output_item.added', 'output_index':1, 'item':{'phase':'final_answer'}},
            {'type':'response.output_text.delta', 'output_index':1, 'delta':'안녕'},
            {'type':'response.completed', 'response':response('안녕')},
        ]
        body = ''.join('event: '+e['type']+'\ndata: '+json.dumps(e,ensure_ascii=False)+'\n\n' for e in events)
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,text=body)))
        pieces = []
        with patch('agent.openai_client.httpx.AsyncClient', return_value=client):
            text, usage = await OpenAILLM('gpt-6-luna', api_key='test').stream('s','u',pieces.append)
        self.assertEqual(text, '안녕')
        self.assertEqual(pieces, ['안녕'])
        self.assertEqual(usage['tokens'], 15)

    async def test_stream_without_completed_event_fails(self):
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,text='data: [DONE]\n\n')))
        with patch('agent.openai_client.httpx.AsyncClient', return_value=client):
            with self.assertRaisesRegex(RuntimeError, 'before response.completed'):
                await OpenAILLM('gpt-6-luna', api_key='test').stream('s','u')

    async def test_server_openai_mode_keeps_shared_mcp_and_reports_effective_model(self):
        class MCP:
            stopped = False
            def __init__(self, **kwargs): pass
            async def start(self): pass
            async def stop(self): self.stopped = True
        with TemporaryDirectory() as folder, patch.dict(os.environ, {'LANGSMITH_TRACING':'false', 'LANGCHAIN_TRACING_V2':'false'}):
            app = create_app(GameConfig(data_dir=Path(folder)), backend='openai', mcp_factory=MCP)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
                    health = (await client.get('/health')).json()
                    self.assertEqual(health['llm_model'], 'gpt-6-luna')
                    self.assertEqual(health['llm_backend'], 'openai')
                    self.assertEqual(health['llm_router_model'], 'gpt-6-luna')
                    game = (await client.post('/games', json={'ai_ids':['A','B'], 'human_id':None})).json()
                    self.assertEqual(game['game']['backend'], 'openai')
                    self.assertEqual(app.state.games.config.llm_backend, 'openai')
                mcp = app.state.games.mcp
            self.assertTrue(mcp.stopped)
