# LLM 백엔드 설정과 연결

게임 엔진/그래프/PokerLLM은 같은 complete/stream 계약을 사용한다.
공급자별 클라이언트를 선택하는 코드는 `agent/llm_controller.py`에 모았다.
OpenAI 기본 모델은 공식 ID `gpt-6-luna`다. [공식 모델 문서](https://developers.openai.com/api/docs/models/gpt-6-luna)

## 설정

`agents/config.py`의 GameConfig에서 바꾼다.

```python
llm_backend: str = 'ollama'                 # 직접 호출/실험의 기본 공급자
ollama_model: str = 'qwen3:8b'
router_model: str = 'qwen3.5:2b'             # Ollama 발언 해석 모델
openai_model: str = 'gpt-6-luna'
openai_router_model: str | None = None      # None이면 openai_model과 동일
openai_max_output_tokens: int = 1024
openai_router_max_output_tokens: int = 768
openai_reasoning_effort: str | None = 'none'
openai_timeout: float = 120.0
```

OpenAI 키는 `OPENAI_API_KEY` 환경변수나 루트 `.env`에서 읽는다. config/실험 manifest에 키를 넣지 않는다.
서버/데모/실험 CLI는 `.env`를 읽는다. 라이브러리를 직접 호출할 때는 호출자가 환경을 준비한다.
서버는 `OPENAI_MODEL`, `OPENAI_ROUTER_MODEL` 환경변수로 config의 모델명을 재정의할 수도 있다.

```sh
# 웹 연결: 기존 서버의 실행 모드를 OpenAI로 지정
LLM_BACKEND=openai .venv/bin/python server.py

# 웹 없이 에이전트 둘로 한 라운드
.venv/bin/python -m agents.demo --mode openai --players 2 --rounds 1

# 실험도 같은 팩토리를 사용한다. 먼저 소량 확인 가능
.venv/bin/python -m experiments.run --stage 1 --backend openai --cases 1
```

서버/데모의 fake 기본 동작은 유지한다. 서버의 `LLM_BACKEND`, 데모의 `--mode`, 실험의 `--backend`를
명시하면 그 실행의 공급자를 선택한다. 실험에서 --backend를 생략하면 config.llm_backend를 따른다.
`--model`은 선택한 공급자의 모델을 재정의한다. 자동으로 다른 공급자나 다른 모델로 fallback하지 않는다.

OpenAI는 판단/발언 해석을 담당한다. **RAG 임베딩은 기존 Ollama embeddinggemma를 계속 사용한다.**
따라서 검색을 사용하는 실행에는 기존 임베딩 서버/Chroma 준비가 필요하다.

## 파일별 역할

| 파일 | 책임 |
|---|---|
| agent/llm_controller.py | LLMClient 프로토콜, create_llm(config, role), configure_backend/configure_model, 실험 사전 점검 |
| agent/ollama_client.py | 기존 Ollama HTTP/NDJSON 구현 그대로 재사용 |
| agent/openai_client.py | Responses API HTTP/SSE, 구조화 스키마 전송 변환, 응답 복원, usage |
| agents/player_llm.py | 세 개인 메모 자동 첨부 후 선택한 클라이언트 호출 |
| agents/speech_interpreter.py | 같은 팩토리의 router 역할을 사용해 인간 발언 해석 |
| agents/game_service.py / server.py | 기존 fake/실모델 실행 조립과 공용 MCP 수명 유지. health에 유효 모델명 제공 |
| experiments/backends.py | 같은 팩토리를 관찰 래퍼로 감싸 기존 시간·입력·출력 로그 유지 |

새 공급자를 연결할 때 업무 노드 대신 controller와 해당 통신 어댑터를 추가한다.
`poker_llm.py`, `graph.py`, Table/Betting/Round의 판단 계약과 게임 규칙은 바꾸지 않았다.
이전 운영 에이전트용 `_old/agent/openai_llm.py`에 의존하지 않는다.

## OpenAI 구조화 출력과 오류 처리

기존 판단 스키마의 최상위 anyOf는 OpenAI가 직접 허용하지 않으므로 어댑터가
`{"result": 기존_스키마}`인 object로 감싼다. 정의와 참조를 보존하고 object 필드를 required로
정규화한다. 응답을 받은 뒤 result만 풀어 원래 JSON 문자열로 반환한다. 그래프는 같은
AgentDecision을 검증하고 엔진은 같은 방식으로 행동을 접수한다.
[Structured Outputs 규칙](https://developers.openai.com/api/docs/guides/structured-outputs)

GPT-6 Luna의 기본 reasoning effort는 이 앱에서 none으로 설정했다. none일 때 temperature=0을
보내며 다른 reasoning effort를 설정하면 temperature는 생략한다. 다른 OpenAI 모델을 선택할 때는
해당 모델이 지원하는 effort를 config에서 지정한다.
[공식 GPT-6 호환성 안내](https://developers.openai.com/api/docs/guides/latest-model)

키 누락/HTTP 오류/출력 한도 초과/거절/완료되지 않은 스트림은 명시적으로 실패한다.
잘린 JSON을 임의로 보정해 베팅하지 않는다. 이후의 검증 재시도/에이전트 실패/자동 행동은
기존 그래프와 runner가 처리한다. 스트림에서는 최종 답변 delta와 완료 이벤트를 읽는다.

usage에는 실제 입력/출력/전체 토큰과 응답 모델 ID를 남긴다. 기본 usd는 None이다.
필요할 때만 openai_usd_per_1k_tokens에 비교용 블렌디드 단가를 설정할 수 있으며,
그 추정값은 실제 청구 금액과 구분한다. 새 SDK/의존성은 추가하지 않고 기존 httpx를 사용했다.

## 검증

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_llm_controller.py'
.venv/bin/python -m unittest discover -s tests -p 'test_experiments.py'
```

팩토리 역할별 선택, 스키마 불변성과 전송 변환, JSON 복원, HTTP/거절/잘림 오류, SSE,
OpenAI 서버 조립/health, 기존 실험 배선을 모의 응답으로 확인한다.
실제 연결 검증의 범위와 실행 ID는 루트 dev_log.md와 exp_log.md에 기록한다.
