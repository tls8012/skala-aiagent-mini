"""임시 포트/저장소의 fake 백엔드와 실제 웹 프록시를 Node 세션에 연결한다.

실행: .venv/bin/python tests/run_web_integration.py
실제 모델/MCP를 호출하지 않는다. 로컬 포트를 열 수 있는 환경이 필요하다.
"""
import asyncio
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['LANGSMITH_TRACING'] = 'false'
os.environ['LANGCHAIN_TRACING_V2'] = 'false'

import uvicorn
from agents.config import GameConfig
from server import create_app
import web_demo_server as proxy


async def start(app):
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=0, log_level='warning'))
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            await task
            raise RuntimeError('test server did not start')
        await asyncio.sleep(.01)
    return server, task, server.servers[0].sockets[0].getsockname()[1]


async def main():
    running = []
    with TemporaryDirectory() as folder:
        try:
            backend, task, port = await start(create_app(GameConfig(data_dir=Path(folder), target_chips=999, seed=7), backend='fake'))
            running.append((backend, task))
            proxy.BACKEND = f'http://127.0.0.1:{port}'
            web, task, port = await start(proxy.app)
            running.append((web, task))
            child = await asyncio.create_subprocess_exec('node', str(ROOT / 'web/scripts/check-live-session.mjs'), f'http://127.0.0.1:{port}', cwd=ROOT)
            try:
                code = await asyncio.wait_for(child.wait(), 40)
            except BaseException:
                if child.returncode is None:
                    child.kill()
                    await child.wait()
                raise
            if code:
                raise RuntimeError(f'Node integration failed: {code}')
        finally:
            for server, _ in running:
                server.should_exit = True
            await asyncio.gather(*(task for _, task in running), return_exceptions=True)


if __name__ == '__main__':
    asyncio.run(main())
