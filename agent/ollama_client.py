"""Ollama HTTP/NDJSON 통신과 토큰 사용량 계산. 업무 규칙은 포함하지 않는다."""
import json

import httpx

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"


class OllamaLLM:
    def __init__(self, model: str, usd_per_1k_tokens: float, *,
                 think: bool | None = None, num_predict: int | None = None):
        self.name = f"ollama:{model}"
        self.model = model
        self.usd_per_1k = usd_per_1k_tokens
        self.think = think
        self.num_predict = num_predict

    def _payload(self, system: str, user: str, *, stream: bool,
                 schema: dict | None = None) -> dict:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": stream,
            "options": {"temperature": 0.0},
        }
        if self.think is not None:
            payload["think"] = self.think
        if self.num_predict is not None:
            payload["options"]["num_predict"] = self.num_predict
        if schema is not None:
            payload["format"] = schema
        return payload

    def _usage(self, data: dict) -> dict:
        tokens = (data.get("prompt_eval_count") or 0) + (data.get("eval_count") or 0)
        return {"model": self.name, "tokens": tokens,
                "usd": round(tokens / 1000 * self.usd_per_1k, 6)}

    def complete(self, system: str, user: str, schema: dict | None = None) -> tuple[str, dict]:
        payload = self._payload(system, user, stream=False, schema=schema)
        resp = httpx.post(OLLAMA_URL, json=payload, timeout=300.0)
        resp.raise_for_status()
        data = resp.json()
        content = data["message"]["content"]
        return content, self._usage(data)

    async def stream(self, system: str, user: str, on_token=None) -> tuple[str, dict]:
        """NDJSON을 읽으며 텍스트 조각을 콜백으로 전달한다."""
        payload = self._payload(system, user, stream=True)
        chunks: list[str] = []
        usage = self._usage({})
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", OLLAMA_URL, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    piece = data.get("message", {}).get("content", "")
                    if piece:
                        chunks.append(piece)
                        if on_token is not None:
                            on_token(piece)
                    if data.get("done"):
                        usage = self._usage(data)
        usage["prompt"] = f"[SYSTEM]\n{system}\n\n[USER]\n{user}"
        return "".join(chunks), usage


