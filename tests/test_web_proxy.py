"""웹 프록시의 전달 계약과 SSE/오류 자원 정리를 검사한다."""
import json
import unittest
from unittest.mock import patch

import httpx
import web_demo_server as web

AsyncClient = httpx.AsyncClient


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client=AsyncClient(transport=httpx.ASGITransport(app=web.app),base_url='http://web')
        self.addAsyncCleanup(self.client.aclose)

    def backend(self,handler):
        clients=[]
        def factory(**kwargs):
            client=AsyncClient(transport=httpx.MockTransport(handler),**kwargs)
            clients.append(client)
            return client
        return patch.object(web.httpx,'AsyncClient',side_effect=factory),clients

    async def test_game_routes_forward_method_body_query_and_headers(self):
        received=[]
        async def handler(request):
            received.append(request)
            return httpx.Response(202,json={'ok':True})
        backend,clients=self.backend(handler)
        with backend:
            for method,path in [('POST','/games'),('POST','/games/id/rounds'),
                                ('POST','/games/id/commands'),('POST','/games/id/commands/release'),
                                ('GET','/games/id'),('DELETE','/games/id')]:
                response=await self.client.request(method,path+'?after=3',
                    headers={'Authorization':'Bearer original-token'},json={'round_id':1})
                self.assertEqual(response.status_code,202)
                request=received[-1]
                self.assertEqual(request.method,method)
                self.assertEqual(str(request.url),web.BACKEND+path+'?after=3')
                self.assertEqual(request.headers['authorization'],'Bearer original-token')
                self.assertEqual(json.loads(request.content),{'round_id':1})
        self.assertTrue(all(client.is_closed for client in clients))

    async def test_health_reports_proxy_and_backend_independently(self):
        async def handler(request):
            self.assertEqual(str(request.url),web.BACKEND+'/health')
            return httpx.Response(200,json={'status':'ok','llm_backend':'fake'})
        backend,clients=self.backend(handler)
        with backend:
            response=await self.client.get('/health')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['proxy'],{'status':'ok','html_available':True})
        self.assertEqual(response.json()['backend']['details']['llm_backend'],'fake')
        self.assertEqual(response.json()['backend']['status'],'ok')
        self.assertTrue(all(client.is_closed for client in clients))
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as folder:
            backend,_=self.backend(handler)
            with backend,patch.object(web,'HERE',Path(folder)):
                response=await self.client.get('/health')
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json()['proxy']['status'],'degraded')
        self.assertEqual(response.json()['backend']['status'],'ok')

    async def test_backend_health_failure_keeps_local_health_visible(self):
        for error,expected in [(httpx.ConnectError,'unavailable'),(httpx.ConnectTimeout,'timeout')]:
            async def handler(request):
                raise error('offline',request=request)
            backend,clients=self.backend(handler)
            with backend:
                response=await self.client.get('/health')
            self.assertEqual(response.status_code,503)
            self.assertEqual(response.json()['proxy']['status'],'ok')
            self.assertEqual(response.json()['backend']['status'],expected)
            self.assertTrue(all(client.is_closed for client in clients))
        for status,body,expected in [(503,{'status':'failed'},'unhealthy'),(200,None,'invalid_response')]:
            async def handler(request):
                return httpx.Response(status,json=body) if body is not None else httpx.Response(status,text='not JSON')
            backend,_=self.backend(handler)
            with backend:
                response=await self.client.get('/health')
            self.assertEqual(response.status_code,503)
            self.assertEqual(response.json()['backend']['status'],expected)

    async def test_sse_forwards_frames_cursor_and_closes_upstream(self):
        closed=[]
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'id: 4\nevent: Speech\ndata: {"type":"Speech"}\n\n'
                yield b': keep-alive\n\n'
            async def aclose(self):
                closed.append(True)
        async def handler(request):
            self.assertEqual(request.headers['last-event-id'],'3')
            self.assertEqual(request.headers['authorization'],'Bearer token')
            self.assertEqual(request.url.params['after'],'2')
            return httpx.Response(200,headers={'content-type':'text/event-stream',
                'cache-control':'no-cache','x-accel-buffering':'no'},stream=Stream())
        backend,clients=self.backend(handler)
        with backend:
            response=await self.client.get('/games/id/events?after=2',headers={
                'Authorization':'Bearer token','Last-Event-ID':'3'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['content-type'],'text/event-stream')
        self.assertEqual(response.headers['x-accel-buffering'],'no')
        self.assertIn('id: 4\n',response.text)
        self.assertIn(': keep-alive\n\n',response.text)
        self.assertTrue(closed)
        self.assertTrue(all(client.is_closed for client in clients))

    async def test_stream_auth_error_keeps_http_status_json_and_challenge(self):
        async def handler(request):
            return httpx.Response(401,json={'detail':'game access token required'},
                                  headers={'WWW-Authenticate':'Bearer'})
        backend,clients=self.backend(handler)
        with backend:
            response=await self.client.get('/games/id/events')
        self.assertEqual(response.status_code,401)
        self.assertEqual(response.headers['www-authenticate'],'Bearer')
        self.assertEqual(response.json(),{'detail':'game access token required'})
        self.assertTrue(all(client.is_closed for client in clients))

    async def test_connection_failure_and_timeout_are_gateway_errors(self):
        for error,status in [(httpx.ConnectError,502),(httpx.ConnectTimeout,504)]:
            async def handler(request):
                raise error('unavailable',request=request)
            backend,clients=self.backend(handler)
            with backend:
                response=await self.client.get('/games/id/events')
            self.assertEqual(response.status_code,status)
            self.assertTrue(all(client.is_closed for client in clients))


if __name__=='__main__':
    unittest.main()
