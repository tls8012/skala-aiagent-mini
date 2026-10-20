"""포커 HTTP 어댑터. 수명 관리와 SSE를 유지하고 실제 조립은 agents에 위임한다."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field, model_validator

from agent.mcp_client import MCPClient
from agents.config import GameConfig, export_mcp_config, load_mcp_config
from agents.game_service import GameService, GameSession, ServiceError
from agents.models import Contract, PlayerCommand, safe_id

CREATION_COUNT = 0
bearer = HTTPBearer(auto_error=False)


class CreateGameRequest(Contract):
    ai_ids: list[str] = Field(default_factory=lambda: ['A', 'B', 'C'], min_length=2, max_length=52)
    human_id: str | None = 'human'

    @model_validator(mode='after')
    def validate_seats(self):
        ids = self.ai_ids + ([self.human_id] if self.human_id is not None else [])
        for pid in ids:
            safe_id(pid)
        if len(ids) > 52 or len(ids) != len(set(ids)):
            raise ValueError('seats must be unique and no more than 52')
        return self


class StartRoundRequest(Contract):
    round_id: int = Field(ge=1)


class ReleaseSpeechRequest(Contract):
    round_id: int = Field(ge=1)
    basis_event: int = Field(ge=0)


def _configuration() -> GameConfig:
    config = load_mcp_config()
    changes = {key: os.environ[name] for key, name in
               [('ollama_model', 'OLLAMA_MODEL'), ('router_model', 'ROUTER_MODEL')]
               if os.environ.get(name)}
    return replace(config, **changes)


def create_app(config: GameConfig | None = None, backend: str | None = None, mcp_factory=None):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global CREATION_COUNT
        settings = config if config is not None else _configuration()
        mode = backend if backend is not None else os.environ.get('LLM_BACKEND', 'fake')
        if mode not in {'fake', 'ollama'}:
            raise ValueError('LLM_BACKEND must be fake or ollama')
        CREATION_COUNT += 1
        mcp = None
        service = None
        try:
            if mode == 'ollama':
                mcp = (mcp_factory or MCPClient)(flaky_first_tool_call=False,
                    server_scripts={'poker': Path(__file__).parent / 'agents' / 'mcp_server.py'})
                # 기존 클라이언트가 subprocess 환경을 복사하는 시점에만 설정을 전달한다.
                previous = os.environ.get('POKER_GAME_CONFIG')
                os.environ.update(export_mcp_config(settings))
                try:
                    await mcp.start()
                finally:
                    if previous is None:
                        os.environ.pop('POKER_GAME_CONFIG', None)
                    else:
                        os.environ['POKER_GAME_CONFIG'] = previous
            service = GameService(settings, mode, mcp)
            app.state.games = service
            app.state.instance_id = id(service)
            app.state.creation_count = CREATION_COUNT
            yield
        finally:
            try:
                if service is not None:
                    await service.close()
            finally:
                if mcp is not None:
                    await mcp.stop()
                try:
                    from langchain_core.tracers.langchain import wait_for_all_tracers
                    await asyncio.to_thread(wait_for_all_tracers)
                except Exception:
                    pass

    app = FastAPI(title='Indian Poker Table API', version='1.0', lifespan=lifespan)

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, error: ServiceError):
        return JSONResponse({'detail': error.detail}, status_code=error.status)

    async def session(game_id: str, request: Request,
                credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> GameSession:
        if credentials is None:
            raise HTTPException(401, 'game access token required', headers={'WWW-Authenticate': 'Bearer'})
        return request.app.state.games.get(game_id, credentials.credentials)

    @app.get('/health', summary='서버와 공용 런타임 상태')
    async def health(request: Request):
        service = request.app.state.games
        return {'status': 'ok', 'instance_id': request.app.state.instance_id,
                'creation_count': request.app.state.creation_count, 'llm_backend': service.backend,
                'ollama_model': service.config.ollama_model, 'router_model': service.config.router_model,
                'mcp_connected': service.mcp is not None, 'game_count': service.game_count}

    @app.post('/games', status_code=201, summary='AI 및 선택적 인간 테이블 생성')
    async def create_game(body: CreateGameRequest, request: Request):
        game = request.app.state.games.create(body.ai_ids, body.human_id)
        return {'access_token': game.access_token, 'game': game.snapshot()}

    @app.get('/games/{game_id}', summary='현재 인간 관점 또는 AI-only 공개 상태')
    async def get_game(game: GameSession = Depends(session)):
        return game.snapshot()

    @app.post('/games/{game_id}/rounds', status_code=202, summary='라운드 시작 또는 같은 시작 요청 재확인')
    async def start_round(body: StartRoundRequest, game: GameSession = Depends(session)):
        return await game.start_round(body.round_id)

    @app.post('/games/{game_id}/commands', summary='발언·발언 없음·베팅 제출')
    async def command(body: PlayerCommand, game: GameSession = Depends(session)):
        return await game.command(body)

    @app.post('/games/{game_id}/commands/release', summary='인간의 보류 발언 재확인')
    async def release(body: ReleaseSpeechRequest, game: GameSession = Depends(session)):
        return await game.release(body.round_id, body.basis_event)

    @app.get('/games/{game_id}/events', summary='공개 사건 구독 및 커서 재연결')
    async def events(after: int = Query(default=0, ge=0), last_event_id: str | None = Header(default=None),
                     game: GameSession = Depends(session)):
        if last_event_id is not None:
            try:
                previous = int(last_event_id)
                if previous < 0:
                    raise ValueError()
                after = max(after, previous)
            except ValueError:
                raise HTTPException(422, 'Last-Event-ID must be a nonnegative sequence')
        game.journal.since(after)  # 응답 헤더를 보내기 전에 잘못된/만료된 커서를 거절한다.

        async def stream():
            async for record in game.journal.stream(after):
                if record is None:
                    yield ': keep-alive\n\n'
                else:
                    prefix = f"id: {record['sequence']}\n" if 'sequence' in record else ''
                    yield f"{prefix}event: {record['type']}\ndata: {json.dumps(record, ensure_ascii=False)}\n\n"

        return StreamingResponse(stream(), media_type='text/event-stream', headers={
            'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    @app.delete('/games/{game_id}', summary='테이블 작업 및 구독 종료')
    async def close_game(request: Request, game: GameSession = Depends(session)):
        await request.app.state.games.remove(game.table.game_id, game.access_token)
        return {'game_id': game.table.game_id, 'status': 'closed'}

    return app


app = create_app()

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8100, log_level='warning')
