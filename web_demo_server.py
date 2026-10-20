"""HTML 제공, 상태 점검, 일반 API 중계와 SSE 연결을 담당하는 웹 프록시.

실행: .venv/bin/python web_demo_server.py → http://localhost:8600
server.py는 먼저 8100에 실행한다. 인증 판단과 게임 로직은 백엔드가 담당한다.
"""
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

BACKEND = "http://127.0.0.1:8100"
HERE = Path(__file__).parent

app = FastAPI()
app.mount("/assets", StaticFiles(directory=HERE / "web" / "dist" / "assets", check_dir=False), name="assets")


@app.get("/")
async def index():
    path = HERE / "web" / "dist" / "index.html"
    if not path.is_file():
        return JSONResponse({"detail": "frontend is not built; run npm run build in web/"}, status_code=503)
    return FileResponse(path)


def request_headers(req):
    return {name: req.headers[name] for name in
            ('content-type', 'authorization', 'last-event-id', 'accept') if name in req.headers}


def response_headers(upstream):
    # httpx가 디코딩한 본문에 원본 Content-Length/Content-Encoding을 붙이지 않는다.
    return {name: upstream.headers[name] for name in
            ('content-type', 'cache-control', 'x-accel-buffering', 'www-authenticate', 'allow')
            if name in upstream.headers}


@app.get("/health")
async def proxy_health():
    html_available = (HERE / 'web' / 'dist' / 'index.html').is_file()
    local = {'status': 'ok' if html_available else 'degraded', 'html_available': html_available}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            upstream = await client.get(f'{BACKEND}/health')
        try:
            details = upstream.json()
            valid = isinstance(details, dict)
        except ValueError:
            details, valid = None, False
        healthy = upstream.is_success and valid and details.get('status') == 'ok'
        backend = {'status': 'ok' if healthy else 'unhealthy' if valid else 'invalid_response',
                   'http_status': upstream.status_code, 'details': details}
    except httpx.TimeoutException:
        backend = {'status': 'timeout'}
    except httpx.RequestError:
        backend = {'status': 'unavailable'}
    healthy = local['status'] == 'ok' and backend['status'] == 'ok'
    return JSONResponse({'status': 'ok' if healthy else 'degraded',
                         'proxy': local, 'backend': backend}, status_code=200 if healthy else 503)


# wildcard보다 먼저 등록하여 events만 긴 연결을 사용한다.
@app.get("/games/{game_id}/events")
async def proxy_events(game_id: str, req: Request):
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10.0))
    upstream = None

    async def close():
        try:
            if upstream is not None:
                await upstream.aclose()
        finally:
            await client.aclose()

    try:
        request = client.build_request('GET', f'{BACKEND}/games/{game_id}/events',
            params=req.query_params.multi_items(), headers=request_headers(req))
        upstream = await client.send(request, stream=True)
        if not upstream.is_success:
            try:
                return Response(await upstream.aread(), status_code=upstream.status_code,
                                headers=response_headers(upstream))
            finally:
                await close()
    except httpx.TimeoutException:
        await close()
        return JSONResponse({'detail': 'backend request timed out'}, status_code=504)
    except httpx.RequestError:
        await close()
        return JSONResponse({'detail': 'backend unavailable'}, status_code=502)
    except BaseException:
        await close()
        raise

    async def relay():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await close()

    return StreamingResponse(relay(), status_code=upstream.status_code,
                             headers=response_headers(upstream), background=BackgroundTask(close))


@app.api_route("/games", methods=["POST"])
@app.api_route("/games/{path:path}", methods=["GET", "POST", "DELETE"])
async def proxy_games(req: Request):
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
            upstream = await client.request(req.method, f'{BACKEND}{req.url.path}',
                params=req.query_params.multi_items(), content=await req.body(), headers=request_headers(req))
        return Response(upstream.content, status_code=upstream.status_code, headers=response_headers(upstream))
    except httpx.TimeoutException:
        return JSONResponse({'detail': 'backend request timed out'}, status_code=504)
    except httpx.RequestError:
        return JSONResponse({'detail': 'backend unavailable'}, status_code=502)


if __name__ == "__main__":
    print("브라우저에서 http://localhost:8600 을 열어보세요 (server.py가 8100에 떠 있어야 합니다)")
    uvicorn.run(app, host="0.0.0.0", port=8600)
