# 개발 로그

## 작은 변환

### Ollama 통신과 운영 업무 로직 분리

#### 구조를 나눈 이유와 기존 구조의 문제

기존 구현은 fake 백엔드와 동일한 `route / plan / analyze / chat / write` 인터페이스를
Ollama와 OpenAI에도 제공하는 구조다. 메서드가 나뉜 것은 그래프의 각 단계가 필요로 하는
입력과 결과가 다르기 때문이다. 주석과 호출 구조로 볼 때, 단계별 동작과 모델 분리를
보여주는 실습 목적에 맞춰 백엔드마다 업무 로직을 함께 구현한 것으로 보인다.

공통 호출 함수 `_chat()`과 `_chat_stream()`은 이미 있었다. 하지만 같은 파일과 상속
구조에 HTTP 통신, 프롬프트, 업무 스키마, 시스템 목록 조회, 계획 보정이 들어 있었다.
이 때문에 업무가 바뀌면 모델 백엔드를 수정해야 하고, 백엔드를 추가하면 업무 코드가
복제된다. 함수 이름만 통일하는 것으로는 이 결합을 풀 수 없다.

#### 이번에 적용한 변경

- `agent/ollama_client.py`: `OllamaLLM`이 `complete()`와 `stream()`을 제공한다.
  HTTP/NDJSON 처리, 요청 구성, 토큰과 가상 비용 계산을 담당한다.
  요청 구성과 사용량 계산의 중복을 공통 함수로 묶었다.
- `agent/incident_llm.py`: 운영 업무 프롬프트, JSON 스키마, 출력 보정,
  모니터링 결과의 텍스트 변환을 담당한다. `TextClient` Protocol을 통해
  모델 호출에 필요한 최소 인터페이스를 정의하고 클라이언트를 생성자로 주입받는다.
- `agent/ollama_llm.py`: 기존 `OllamaFastLLM(model, usd_per_1k_tokens)`와
  `OllamaQualityLLM(model, usd_per_1k_tokens)` 생성자를 유지하는 조립 진입점이다.

의존 방향:

```text
server → OllamaFastLLM / OllamaQualityLLM (조립)
                    ├─ IncidentFastLLM / IncidentQualityLLM (업무)
                    │                    └─ TextClient (모델 호출 계약)
                    └─ OllamaLLM (통신, TextClient 구현)

graph → route / plan / analyze / chat / write
```

업무 함수명을 유지한 이유는 분류·계획·작성의 의미와 반환 계약이 서로 다르기 때문이다.
업무 계층 아래에서 모델 호출을 공통화했고, 업무 계층은 특정 모델 제공자를 몰라도 된다.
MCP는 도구·자료·표준 절차를 제공하고, graph는 실행 흐름을 조율하는 기존 역할을 유지한다.

#### 유지한 동작과 범위

그래프의 호출 방식, 서버의 백엔드 선택, 업무 프롬프트, 스키마, 계획 보정,
대화 이력 범위, 답변 콜백, 사용량 반환 형식을 유지했다.
LangSmith 업무 호출 이름은 `ollama_fast.* / ollama_quality.*`에서
`incident_fast.* / incident_quality.*`로 바뀐다. 모델명은 사용량의 `model`에 유지된다.
기존 내부 `_chat / _chat_stream` 호출은 새 통신 인터페이스로 교체했다.

OpenAI와 fake 구현은 이번에 이동하지 않았다. 따라서 OpenAI와의 업무 코드 중복은 남아 있다.
운영 업무 계층은 여전히 `data/monitoring.json`과 운영 데이터 형식을 알고 있다.
분류·계획·분석의 HTTP 호출도 기존처럼 동기 방식이므로 비동기 노드를 막을 수 있다.
이번 변경은 동작을 유지하며 Ollama의 책임 경계를 나누는 데 한정한다.

#### 검증

` .venv/bin/python -m unittest discover -s tests -v ` — 6개 테스트 통과.

- 잘못된 분류 JSON의 복구와 조사 태스크 누락 복구
- 대상 시스템 보정과 중복 태스크 제거
- 대화 이력, 검증 피드백, 토큰 콜백 전달
- 기존 fast/quality 생성자와 모델명
- 구조화 요청, 사용량 계산, HTTP 오류 전파
- NDJSON 스트림 조립과 최종 사용량

## 큰 변환

### 시스템 구조

#### 목적과 재사용 원칙

인디언 포커를 하는 2명 이상의 AI와 선택적으로 참여하는 인간 플레이어를 지원한다.
인간은 테이블 설정에 따라 참가하며, AI 3명과 인간 1명도 가능한 구성이다.
라운드는 배분 → 대화 → 공개·정산으로 진행하고, 대화 중 순서가 있는 베팅을 병행한다.

실제 게임 구동 코드는 `agents/` 아래에 둔다. 기존 `agent/`는 공통 인프라의 공급원으로
사용하고, 필요한 코드를 import·주입하여 재사용한다. 새 시스템을 만든다는 이유로
LLM 클라이언트, MCP 클라이언트, 재시도, 추적, 그래프 실행기, HTTP 서버를 다시 만들지 않는다.
앞서 Ollama의 통신과 업무 로직을 분리한 목적도 게임 업무에서 같은 통신 코드를 쓰기 위함이다.

#### 전체 연결

```text
server.py — 기존 FastAPI·lifespan·HTTP/SSE
    │ 게임 진입점과 시작·종료 연결
    ▼
agents/ — 테이블 진행·게임 규칙·참가자별 정보·게임 업무 설정
    ├─ 기존 그래프 구성·호출 방식 활용 → 참가자 판단·발언·행동 제안
    ├─ agent/ollama_client.py → 기존 LLM HTTP·스트리밍 호출
    ├─ agent/mcp_client.py → 기존 MCP 연결·도구 호출
    │      └─ 게임용 MCP 도구 → 자기 메모장·RAG 조회·경험 저장
    └─ 기존 SQLite·추적·재시도 기반 활용
```

기존 운영 지원 에이전트와 게임 시스템은 공통 인프라를 공유한다.
운영 지원 전용 프롬프트와 모니터링 규칙까지 게임에 그대로 적용한다는 뜻은 아니다.
업무별 차이는 `agents/`의 설정·프롬프트·얇은 연결 코드에 모으고,
공통 계층 변경은 실제로 필요한 지점에 한정한다.

#### 무엇을 유지하고 어떻게 쓰는가

| 기존 구성 | 유지 및 활용 방식 | 필요한 연결·변경 범위 |
|---|---|---|
| `server.py` | FastAPI 앱, 환경변수 로딩, lifespan, app.state 공유, 기존 채팅 API, SSE 전송 방식 유지 | 게임 진입점 등록과 게임 실행의 시작·종료 연결만 추가하는 방향 |
| `agent/graph.py` | StateGraph 구성, 의존성 주입, `ainvoke/astream`, 일반 채팅 분기와 토큰 전달 방식 활용 | 운영 장애 전용 도구명·분류·검증 기준은 게임 업무에 맞게 연결해야 함. 내부 설계는 별도 검토하며 범용 실행기를 새로 만들지 않음 |
| `agent/ollama_client.py` | `complete/stream`, 구조화 출력 전달, HTTP/NDJSON, 사용량 계산 재사용 | 통신 구현을 복제하지 않고 게임 업무에 동일 클라이언트 주입 |
| `agent/incident_llm.py` 및 `agent/ollama_llm.py` | 업무와 통신을 분리하고 조립하는 방식, 일반 채팅 응답 흐름 활용 | 운영 진단 프롬프트·계획 보정은 게임용으로 대체. 기존 운영 클래스 전체를 게임 클래스처럼 사용하지 않음 |
| `agent/mcp_client.py` | MCP 연결 수명, 여러 서버 연결, `call_tool`, Resource/Prompt 탐색·조회 재사용 | 기존 `server_scripts` 인자로 게임 도구 서버를 연결하고 `server` 인자로 호출 대상을 명시. 새 MCP 클라이언트 불필요 |
| `mcp_server.py` | 기존 FastMCP 도구·Resource·Prompt 제공 방식 활용 | 자기 메모장·RAG 도구 구현을 추가하거나 같은 방식의 게임 도구 서버를 `agents/` 아래에 등록. 서버 구성은 구현 시 최소 변경안 선택 |
| `agent/deps.py` | `SupervisorDeps`와 명시적 의존성 주입 방식 활용 | 기존 필드를 우선 사용하고 게임 전용 정보는 게임 상태에서 관리. 필요한 경우에만 의존성 계약 최소 확장 |
| `agent/session_store.py` | 기존 채팅 이력 저장과 SQLite 기반 유지 | 일반 채팅에는 기존 `load/append` 활용. 게임 사건·메모에 필요한 필드는 별도 테이블로 최소 확장하며 기존 채팅 테이블에 억지로 넣지 않음 |
| `agent/retry.py` | 기존 `with_retry` 재사용 | 현재 처리하는 일시적 timeout 범위에서 적용. 베팅 등 상태 변경을 무조건 재시도하지 않음 |
| LangSmith | 기존 `traceable`과 모델·MCP 추적 재사용 | 테이블·라운드·참가자를 식별할 메타데이터 연결 |
| `web_chat.html`, `web_demo_server.py` | 현재 화면·프록시 유지 | 게임 UI는 나중에 추가. 그때 게임 명령과 테이블 이벤트 구독 연결을 확장 |

모델 선택용 환경변수와 fast/quality 분리도 기존 방식을 따른다.
게임용 업무 객체를 조립하는 부분만 연결하고 모델별 HTTP 호출을 추가 구현하지 않는다.
다만 OpenAI 구현은 아직 업무와 통신이 분리되지 않았으므로, 모든 백엔드가 이미 동일한
범용 클라이언트를 제공한다고 가정하지 않는다. 다른 백엔드 지원 시 필요한 부분만 정리한다.

#### 새로 필요한 게임 구동 부분

`agents/`에는 다음 책임만 추가한다. 아래는 책임 구분이며 각각을 별도 파일이나
서비스로 만들기로 확정한 것은 아니다. 처음부터 별도의 프레임워크를 만들지 않는다.

1. 테이블 관리: AI 수, 인간 참여 여부, 좌석, 라운드 시작·종료와 실행 작업 관리.
2. 게임 규칙: 카드 배분, 발언 한도, 베팅 차례·금액 검증, 공개 조건, 승패·정산.
3. 참가자 관찰 정보: 타인의 카드는 보이고 자신의 카드는 보이지 않는 정보 구성.
   AI 입력과 인간의 상태 조회·이벤트에 같은 정보 제한을 적용한다.
4. 대화·행동 접수: 인간 입력과 AI 제안을 받아 게임 규칙에 따라 확정하고 사건 전달.
5. 업무 연결: 기존 그래프·LLM·MCP에 게임 상태와 참가자 정보를 전달하는 얇은 코드.
6. 기억 도구 구현: 자기 메모장, 과거 경험 검색, 라운드 경험 저장을 MCP로 제공.

게임 상태의 최종 변경 권한은 테이블이 가진다. AI와 도구는 판단 자료나 행동 제안을
돌려주고, 카드·판돈·베팅 순서·공개 여부를 임의로 변경하지 않는다.
AI 판단은 동시에 진행할 수 있지만, 제출된 발언과 베팅의 상태 반영은 테이블에서
순서대로 검증·확정한다. 이는 자유 발언에 차례를 부여하는 것과는 다르다.

#### 기존 API·그래프를 활용하는 경계

HTTP 요청 검증과 응답, SSE 인코딩·전송은 기존 FastAPI 기반을 사용한다.
기존 채팅은 요청 하나에 그래프 실행 하나가 끝나는 형태이므로, 테이블의 지속적인 진행까지
그 요청 수명에 묶지는 않는다. 게임은 서버가 관리하는 실행 상태를 유지하고,
입력 전송과 이벤트 구독을 분리하는 최소한의 게임 API 연결이 필요하다.
기존 `/chat`과 `/chat/stream`을 제거하거나 게임 전용으로 바꾸지 않는다.
별도 HTTP 클라이언트 계층이나 WebSocket 서버를 먼저 만들지 않는다.

그래프는 참가자 한 명의 판단 블록으로 취급하며, 참가자별로 독립 실행한다.
기존 라우터 → 일반 채팅 응답 경로를 보존하고, 게임 판단 경로에 필요한 업무 차이만 둔다.
게임 종료·대기 조건을 LLM 추측으로 처리하지 않고 테이블의 명시적 상태로 판정한다.
기존 동기 `complete()`는 여러 AI를 실행할 때 이벤트 루프를 막을 수 있으므로,
구현 시 호출 경계에서 비차단 실행을 확보한다. 이를 위해 통신 클라이언트를 새로 만들지는 않는다.

#### 자기 메모장과 RAG는 MCP 도구로 제공

자기 메모 읽기·쓰기, 과거 라운드 검색, 종료된 라운드의 경험 저장은 MCP 도구로 호출한다.
클라이언트 측에 별도의 메모장 서비스나 벡터 DB 래퍼를 중복 구현하지 않는다.
임베딩 모델과 FAISS/Chroma 연결은 도구 구현 안에 두고, 그래프는 도구의 입출력만 안다.
현재 `MCPClient.call_tool()`의 JSON 응답 해석에 맞춰 도구 결과를 제공한다.

모든 발언과 행동의 원문은 기록하되, 매 발언을 즉시 임베딩하지 않는다.
현재 라운드는 관찰 정보로 직접 전달하고, 종료된 라운드의 상황·발언·행동·공개 결과를
정해진 형식의 경험 문서로 만들어 임베딩한다. 임베딩은 게임 진행을 기다리게 하지 않도록
별도 작업으로 처리하며, 아직 반영되지 않은 기억은 다음 검색에서 누락될 수 있다.

메모와 검색 범위는 테이블·참가자 기준으로 제한한다. 호출 대상 참가자 식별은
테이블이 신뢰할 수 있는 실행 문맥에서 제공하고, 모델이 임의로 다른 참가자 ID를
선택해 개인 메모를 읽도록 두지 않는다. 공용 경험에는 실제 공개된 정보만 담는다.
자유 대화에 참여한다는 이유로 상대의 비공개 판단이나 자신의 미공개 카드를 얻을 수 없다.

#### 게임 진행의 합의 사항

- 대화는 차례 없이 진행하며 동시 제출을 허용한다. 같은 참가자의 연속 발언은 금지한다.
- 라운드당 인간의 모든 발언은 최대 5회다. 잡담과 질문도 차감한다.
- AI의 게임 발언은 최대 5회다. 인간이 요청한 일반 잡담 응답은 차감하지 않는다.
  AI끼리는 잡담을 시작하거나 이어가지 않으며, 일반 채팅 응답도 마지막 발언자를 갱신한다.
- 체크·콜·레이즈·폴드는 발언 한도와 별개다. 인간은 발언 소진 후에도 베팅할 수 있다.
- 대화 중 베팅을 병행하되, 베팅 차례는 A → B → C → … → 인간 순서다.
  자기 차례가 열려 있는 동안 대화를 참고해 행동하고, 폴드한 참가자는 건너뛴다.
  시작 좌석을 라운드마다 회전시키는 것은 후속 작업으로 둔다.
- 최초 베팅은 최소 1, 레이즈 최소 증가량도 1이다. 체크·콜·폴드를 지원한다.
  레이즈 후에는 다른 생존 참가자의 대응이 다시 필요하다.
- 침묵은 시간으로 판정하지 않는다. AI 전원이 발언할 것 없음 또는 한도 소진이고,
  인간이 있다면 한도 소진 또는 UI에서 발언할 것 없음을 선택한 상태일 때 대화가 멎는다.
  새로운 게임 발언·베팅으로 상황이 바뀌면 발언 가능 참가자는 다시 판단할 수 있다.
- 대화가 멎었다는 이유만으로 공개하지 않는다. 모든 필요한 베팅 대응이 끝났거나
  생존자가 한 명이면 공개·정산한다. 남은 발언 기회를 모두 쓸 필요는 없다.

### 그래프 구조

#### 하나의 정의, 참가자별 독립 실행

참가자 그래프의 정의는 공유하고, AI 참가자 N명이 각각 독립된 입력·판단 상태로 실행한다.
A/B/C 전용 그래프 코드를 따로 복제하거나 AI 수에 맞춰 노드를 추가하지 않는다.
핵심은 그래프 객체의 개수보다 참가자별 실행과 상태의 분리다.

큰 그래프 안에서 A/B/C를 병렬 실행하고 전원 완료 후 결과를 모으는 방식도 가능하지만,
그 방식은 발언 시점이 묶여 일괄 답변처럼 움직이기 쉽다. 각 AI가 '발언할 것 없음'을
반환하면 발언자 선택은 해결되지만, 다른 참가자의 판단 완료를 기다리는 문제는 남는다.
자유 대화와 참가자 수 확장을 위해 독립 실행을 선택한다.

한 번의 실행은 라운드 전체가 아니라 '참가자 한 명이 사건을 보고 반응을 결정하는 과정'이다.
실행이 끝나도 참가자가 게임에서 빠지는 것은 아니다. 다음 사건이 오면 다시 실행한다.
인간은 같은 테이블의 참가자이지만 판단 그래프 대신 UI 입력으로 발언·행동을 제출한다.

```text
테이블 진행 코드 — agents/ 내부
    ├─ A의 관찰 정보·사건 → 참가자 그래프 실행 A
    ├─ B의 관찰 정보·사건 → 참가자 그래프 실행 B
    ├─ C의 관찰 정보·사건 → 참가자 그래프 실행 C
    └─ 인간 입력 접수

완료된 제안부터 테이블이 검증·반영
    → 새 사건 기록
    → 반응이 필요한 참가자에게 전달
```

#### 참가자 그래프의 흐름

```text
시작: 사건 + 해당 참가자가 볼 수 있는 정보
    ↓
라우터
    ├─ 인간의 일반 대화 → 일반 채팅 응답 작성 ───────────┐
    └─ 게임 사건 → 발언·베팅 가능 조건 각각 확인         │
                      ↓                                │
                 필요한 기억 조회 (MCP, 생략 가능)       │
                      ↓                                │
                 판단 갱신                              │
                      ↓                                │
                 발언·베팅 제안 구성 ────────────────────┤
                                                       ↓
                                                  출력 검증
                                                ↙          ↘
                                       제한된 수정         제안 반환·종료
                                            └─ 재검증
```

일반 채팅은 기존 라우터 → 채팅 응답 경로를 보존한다. 인간이 지목한 AI가 응답하고,
그 잡담을 이유로 다른 AI에게 잡담 응답을 요청하지 않는다. 사용 가능한 도구가 없는
외부 정보는 확인한 것처럼 답하지 않는다. 일반 잡담에 RAG 조회를 강제하지 않는다.

게임 경로의 기억 조회는 기존 `mcp_client`를 통해 자기 메모장과 과거 경험을 조회한다.
판단 갱신은 새 발언·베팅·조회 결과에 따라 참가자의 잠정 판단을 수정한다.
출력 검증은 제안의 구조와 허용 행동 등을 확인하고, 수정 횟수에는 상한을 둔다.
카드·판돈·발언 횟수의 실제 변경은 그래프 밖 테이블이 최종 검증 후 수행한다.

#### 발언과 베팅은 독립된 판단

그래프는 발언 여부와 베팅 행동을 별도로 반환한다.
'발언할 것 없음 + 콜', '발언함 + 내 베팅 차례가 아니므로 베팅 제안 없음'이 모두 가능하다.
직전 발언자이거나 게임 발언을 5회 소진해도 베팅은 가능하다.
게임 발언 한도를 소진한 AI도 인간의 일반 질문에는 응답할 수 있다.

출력에는 참가자·라운드 식별자, 판단 기준 사건 번호, 발언 종류·내용 또는 발언할 것 없음,
베팅 제안 여부와 행동·금액을 구분해 담는 방향으로 잡는다. 정확한 스키마는 구현 시 확정한다.
공개할 발언과 개인 판단·메모를 분리하며, 개인 정보가 테이블 공개 이벤트에 섞이지 않도록 한다.

#### 사건에 따른 실행과 상태 관리

| 사건 | 반응 대상 |
|---|---|
| 배분 완료 | 모든 AI |
| 새 게임 발언 | 다른 생존 AI |
| 베팅 발생 | 영향을 받는 생존 AI |
| 자기 베팅 차례 도착 | 해당 AI |
| 인간이 특정 AI에게 일반 질문 | 해당 AI의 일반 채팅 경로 |
| 공개·정산 완료 | 경험 저장·메모 처리 |

같은 참가자의 게임 판단을 무제한 중복 실행하지 않는다. 판단 중 도착한 사건은 모아두고,
현재 작업 이후 최신 상황을 처리한다. 다른 참가자의 실행 완료까지 함께 기다리지는 않는다.
매 사건마다 기존 판단을 취소·재시작하여 베팅이 영원히 확정되지 않는 구조도 피한다.

판단 기준 사건 번호를 이용해 제출 시점에 상황이 바뀌었는지 확인한다.
발언은 앞선 대화를 보고 작성한 말일 수 있지만, 현재의 연속 발언 금지·횟수 제한은 지켜야 한다.
베팅은 현재 차례·금액을 다시 확인하고, 오래된 상황의 제안이 무효라면 최신 정보로 재판단한다.

'발언할 것 없음'은 현재 게임 상황에 대한 결과로 테이블에 기록한다.
전원이 해당 상태 또는 한도 소진이면 추가 발언 판단을 멈추고, 새 게임 사건이 생기면
필요한 참가자가 다시 판단한다. 그래프 내부에서 시간을 재거나 무한 반복으로 발언 의사를 묻지 않는다.
판단 중인 참가자를 발언할 것 없음으로 간주하지 않으며, 대화 정지와 베팅 종료는 별도로 판정한다.
인간 입력 대기는 테이블 상태로 관리하고 다른 AI의 실행 전체를 멈추지 않는다.

#### 기존 구현과의 대응 및 연결 순서

- `route → chat_answer`: 일반 대화 경로 보존.
- `plan → research → analysis`: 게임의 기억 조회·판단 갱신에 맞게 재구성.
- `write → validate`: 발언·행동 제안 생성과 검증에 활용.
- `finalize`: 최종 답변 대신 이번 반응의 제안을 반환.
- LLM 호출, MCP 호출, 의존성 주입, 추적·재시도는 기존 코드 재사용.

게임 발언은 테이블의 승인 전에 생성 토큰을 공개하지 않는 방향으로 시작한다.
생성 중 다른 발언이 확정되면 연속 발언 제한 등에 걸릴 수 있기 때문이다.
일반 채팅의 토큰 스트리밍은 기존 방식을 활용하되 동일한 발언 규칙을 적용한다.

먼저 서버 없이 테이블과 참가자 그래프가 한 라운드를 진행하는 기능을 만든다.
`graph`, 게임용 업무 LLM 파일(기존 `incident_llm`에 대응), 새 게임 MCP 서버를 중심으로
작업하고, `deps`, `ollama_client`, `mcp_client`, `retry`, `session_store`는 최대한 유지한다.
이후 실제 필요한 입력·이벤트 통로에 맞춰 `server.py`, `web_demo_server.py`,
`web_chat.html`의 엔드포인트와 UI를 최소한으로 연결한다.

### 게임 기반 구현 — 그래프 연결 전

#### 구현 범위와 연결점

게임 코드는 하위 패키지 없이 `agents/`에 두었다. 기존 `agent/`, `server.py`,
`web_demo_server.py`, `web_chat.html`의 실행 코드는 수정하지 않았다.
LangGraph 정의·노드·게임 프롬프트·AI 실행 스케줄러도 구현하지 않았다.
`Table`은 규칙과 상태의 조율 객체이며, 참가자 그래프를 언제 실행할지는 연결 코드가 결정한다.
서버 없이 명령을 제출하여 라운드를 끝내고, 사용자 그래프의 결과를 적용할 수 있다.

| 파일 | 역할·주요 객체 | 연결점 |
|---|---|---|
| `agents/config.py` | 불변 dataclass `GameConfig` | `Table(config=...)`와 `GameTools(config=...)`에 주입. 칩·목표·발언·컨텍스트·기억·임베딩 설정 |
| `agents/models.py` | 내부 `Player` dataclass, 경계용 Pydantic `Bet`, `PlayerCommand`, `Observation`, `AgentDecision`, `CommandResult` | 그래프 입력은 `Observation`, 출력은 `AgentDecision`. 명령의 출처는 모델이 아닌 호출 코드가 지정 |
| `agents/cards.py` | `Card`, `Deck`, `find_winner()` | rank 2..14(A), suit C/H/D/S. 2>A 예외와 같은 숫자의 문양 비교. 모든 상대를 이기는 카드가 없으면 무승부 |
| `agents/betting.py` | `Betting` | `act()`, `allowed()`, `to_call()`, `complete`. 참가비·부족액 대출·베팅 순환·레이즈 후 재대응 |
| `agents/conversation.py` | `Conversation` | 발언 횟수·최소 게임 발언·침묵 상태·보류 발언·일반 채팅 응답 자격·최근 메시지 관리 |
| `agents/round.py` | `Round` | `Betting`과 `Conversation` 조합. `betting_open`, `settle()`로 라운드 종료·상환·탈락·이월 판돈 계산 |
| `agents/table.py` | `Table` | 아래의 공개 메서드로 그래프·UI·MCP 연결. 동시 제출의 상태 변경은 lock 안에서 직렬 확정 |
| `agents/mcp_server.py` | `GameTools`, FastMCP 도구 6개 | 개인 텍스트 메모와 공개 경험 저장·Chroma 검색. 상세는 아래 참조 |
| `agents/seed_rounds.json` | 고정된 공개 경험 20판 | 고카드·문양·순환 무승부·블러핑/폴드의 변형. 엔진으로 정상 종료시킨 기록이며 `source=seed`로 구분 |
| `agents/demo.py` | `main()` | `.venv/bin/python -m agents.demo`로 LLM 없이 한 라운드를 진행하고 공개 결과 출력 |
| `agents/requirements-rag.txt` | 선택 의존성 | Chroma 검색에 필요한 패키지. 카드게임·메모 도구만 사용하면 설치 불필요 |
| `tests/test_poker.py` | 규칙·메모·색인·실제 MCP 연결 테스트 | 기존 Ollama 회귀 테스트와 함께 unittest로 실행 |

`Table`의 주요 메서드:

- `Table(ai_ids, human_id=None, config=None, game_id=None)`: AI 최소 2명, 인간 선택 참가.
  같은 데이터 경로에 기존 기록이 있는 game_id는 거절한다. 중단 게임 복구는 구현하지 않았다.
- `start_round()`: 생존자 배분·참가비 납부. 다음 라운드에는 시작 좌석을 한 칸 회전한다.
  `cards=`는 규칙 테스트용 카드 주입 지점이며 일반 참가자 입력으로 공개하지 않는다.
- `observe(player_id)`: 해당 참가자 관점의 Pydantic 입력. 현재 라운드 최근 메시지만 포함하고
  카드·판돈·차례·기여액·가능 행동은 별도 필드로 항상 전달한다.
- `submit(player_id, command)`: 인간·AI 공통 명령 접수. accepted/rejected/deferred 반환.
  같은 참가자의 같은 request_id와 내용은 중복 적용하지 않고 기존 결과를 반환한다.
- `apply_decision(player_id, decision, request_id)`: 그래프 출력을 발언·베팅 명령으로 연결한다.
  참가자 ID와 고유 실행 ID는 호출 코드가 제공한다. 동일 결정의 중복 적용도 막는다.
- `events_since(event_id)`: 현재 라운드의 확정된 공개 사건만 복사해 반환한다.
  참가자 실행·UI 갱신에 사용한다. 과거 라운드는 저장된 기록에서 확인한다.
- `release_deferred(player_id, basis_event)`: 다른 참가자가 말한 뒤 최신 상태에서 보류 발언을
  재확인하여 해제한다. 보류 발언을 자동으로 공개하지 않는다.
- `fail_agent(player_id)`: '나 죽었음' 시스템 사건을 남긴다. 이후 해당 AI 차례에는
  추가 지출이 없는 체크, 아니면 폴드를 자동 적용한다. 게임상 탈락과 추론 실패는 별도 상태다.
- `call_player_tool(mcp, player_id, name, arguments)`: 모델 인자와 출처를 분리해 MCP 호출.
- `await publish_memories(mcp)`: 종료 기록을 기억 도구로 전달한다. 서버/진행 연결 코드가
  라운드 종료 후 호출해야 하며, 엔진이 네트워크나 임베딩을 기다리지는 않는다.

그래프는 `Observation.model_dump()`를 입력받고, 반환 데이터가 `AgentDecision` 계약에
맞도록 작성하면 된다. `AgentDecision`에는 round_id와 basis_event, speech,
text/channel/target/reply_to, bet, wait_for_dialogue를 구분해 둔다.
`wait_for_dialogue`는 실행 연결부를 위한 의사 표시다. 엔진이 스스로 그래프를 반복 실행하지 않는다.
베팅의 `amount`는 총 지출액이 아니라 **현재 최고 베팅액에 더하는 증가량**이다.
체크·콜·폴드에는 amount=0만 허용한다.

#### 이번 구현에서 고정한 MVP 동작

- 목표 칩은 `config.target_chips`를 직접 사용한다. 기본 60이며 참가자 수에 따른 자동 계산은 하지 않는다.
- 참가비는 모두 같은 금액으로 먼저 낸다. 베팅 contributions/current_bet은 참가비를 제외한
  추가 베팅액이고, pot에는 참가비와 이전 무승부 판돈을 포함한다.
- 최소 게임 발언을 각자 한 번 마친 뒤 베팅을 연다. 그 이후에는 발언과 베팅을 병행한다.
  추론 실패 AI는 최소 발언 조건에서 제외한다.
- 인간이 잡담만으로 한도를 소진해 최소 게임 발언을 못 하는 상황을 막기 위해,
  아직 필요한 최소 게임 발언 횟수만큼 마지막 발언 기회를 예약한다.
- AI의 일반 채팅 응답은 미응답 인간 질문의 request_id를 reply_to로 지정해야 한다.
  한 질문당 한 번만 응답할 수 있고, 일반 채팅을 자발적으로 무한 생성할 수 없다.
- 모든 발언은 마지막 발언자를 갱신한다. 일반 채팅의 AI 응답은 게임 발언 횟수를 차감하지 않는다.
- 발언·베팅은 별도 계약이다. 현재 차례에서 대화가 멎었다면 진행 코드는 베팅 결정을 요청해야 한다.
- 오래된 베팅·침묵 결정은 rejudge=True로 거절한다. 이미 끝난 라운드의 그래프 출력도 거절한다.
- 정산은 승자에게 판돈 지급 → 대출 상환 → 잔액 0 이하 탈락 순이다.
  갚지 못한 대출은 탈락자의 debt에 남겨 기록하고, 임의로 다른 참가자에게 전가하지 않는다.
- 무승부는 판돈을 그대로 이월한다. 최종 생존자 한 명 또는 목표 칩 도달자가 있으면 게임 종료,
  전원 탈락이면 승자 없이 종료한다. 종료 시 남은 이월액도 기록에 보존한다.
- 폴드하지 않은 카드만 공개 결과와 라운드 기록에 담는다. 배분 당시의 전체 비공개 카드
  맵이나 개인 메모는 공개 이벤트·원문 기록·RAG 경험에 저장하지 않는다.

#### mcp_server.py — 도구 구현 상세

**구성 및 재사용**

`GameTools`가 텍스트 파일 저장과 경험 보관·색인을 담당하고, 파일 하단의 FastMCP 도구가
그 메서드를 호출한다. 작은 메모 저장을 위해 별도 서비스/리포지토리 계층을 만들지 않았다.
기존 `agent.mcp_client.MCPClient`를 그대로 사용한다. `server_scripts`에
`{"poker": Path("agents/mcp_server.py").resolve()}`를 전달하고 start/stop을 기존처럼 호출한다.
기존 ops/docs 서버도 필요하면 같은 매핑에 함께 넣는다. 게임 호출에는 server="poker"를 명시한다.

도구 반환값은 dict이며 FastMCP가 JSON으로 직렬화하므로, 기존 call_tool의 JSON 해석과 맞는다.
도구 서버는 스크립트 경로로 직접 실행할 수 있도록 프로젝트 import 경로를 설정한다.

**도구 계약**

| 도구 | 입력 | 결과·역할 |
|---|---|---|
| `read_memo` | game_id, agent_id, round_id, scope | 해당 개인의 메모 text. 파일이 없으면 빈 문자열 |
| `write_memo` | 위 출처 + scope, text, append=False | 덮어쓰기 또는 줄바꿈 후 추가. 설정의 최대 글자 수 초과 시 거절 |
| `search_experiences` | 위 출처 + query, limit=3 | ready/indexing/unavailable 상태, 검색 문서·출처·거리. 검색 장애는 빈 결과와 오류로 반환 |
| `memory_status` | game_id, agent_id, round_id | 경험 개수, 색인 진행 여부, 최근 색인 오류 |
| `store_experience` | 완료된 공개 record | 테이블 전용 저장. 원문 경험 저장 후 색인 예약. 플레이어용 도구 목록에는 포함하지 않음 |

**개인 메모 파일과 출처**

scope는 round/game/long이다. 데이터 루트 아래 다음 UTF-8 파일을 사용한다.

```text
memos/<agent_id>/<game_id>/rounds/<round_id>.txt  # 이번 라운드 판단
memos/<agent_id>/<game_id>/game.txt              # 이번 판 전체 판단
memos/<agent_id>/opponents.txt                  # 상대 대처 장기 기억
```

라운드/게임 ID로 파일을 나누므로 다음 라운드의 round 메모는 빈 상태로 시작하고,
이전 파일은 기록으로 남는다. 장기 메모는 같은 agent_id이면 게임을 넘어 유지된다.
새 인격의 AI를 만들 때는 agent_id도 새로 부여해야 한다.
파일 갱신은 서버 내부 lock과 임시 파일 교체를 사용한다. 원문 대화는 별도로 보존하므로
메모는 모델이 요약·수정하는 짧은 개인 기억으로 사용할 수 있다.

출처는 **신뢰된 그래프 실행 코드가** `Table.call_player_tool()`에 player_id로 지정한다.
모델에게 전달할 도구 인자에는 scope/text/query 등만 포함한다. 모델이 game_id/agent_id/
round_id를 전달하면 helper가 거절하며, 실제 출처는 helper가 현재 테이블 상태에서 붙인다.
ID에는 영문·숫자·밑줄·하이픈만 허용하고 도구가 경로를 구성한다.

이는 애플리케이션의 출처 바인딩이며 외부 사용자를 인증하는 보안 프로토콜은 아니다.
공유 stdio 연결만으로 출처 AI를 자동 판별할 수는 없다. 그래프에서 전체 MCP 도구 스키마를
모델에 그대로 노출해 출처 인자를 선택하게 하지 말고 helper를 통해 호출해야 한다.
HTTP 인증과 플레이어별 접근 권한 연결은 향후 서버 작업에서 담당한다.

**라운드 기록 → 경험 저장 → 임베딩**

1. `Table`은 정산 시 공개 발언·행동·결과·정산된 참가자 상태를
   `games/<game_id>/round_000001.json`에 저장한다. 현재 컨텍스트가 잘려도 원문은 남는다.
2. 진행 코드가 `await table.publish_memories(mcp)`를 호출한다.
3. `store_experience`는 완료 이벤트, 사건 순서, 공개 결과, 폴드 카드 제외를 검사한다.
   개인 메모나 전체 카드 같은 알 수 없는 필드는 받지 않는다.
4. `experiences/<game_id>.json`에 테이블별 최근 20판을 보관한다.
   첫 접근 시 하드코딩된 seed 경험 20판으로 시작하며 실제 경험이 들어오면 오래된 것부터 밀어낸다.
   출처는 seed/played로 구분하고, 같은 라운드 재전송으로 중복 삽입하지 않는다.
5. 별도 단일 작업자에 색인을 예약하고 즉시 응답한다. Table은 저장 성공 표식 `.sent`를
   남겨 중복 전송을 줄인다. 전송 실패 시 다음 publish 호출로 다시 보낼 수 있다.
6. 작업자는 게임 발언 일부·베팅 행동·공개 결과를 정해진 경험 문서로 구성한다.
   문서당 발언은 앞 240자씩 사용하며, 원문 기록은 줄이지 않는다.
7. Ollama `/api/embed`에 여러 신규 문서를 한 번에 보내고 Chroma에 명시적 벡터로 저장한다.
   이미 색인한 문서는 다시 임베딩하지 않고, 보관 범위를 벗어난 문서는 색인에서도 삭제한다.

검색은 같은 테이블의 현재 보관 경험으로 제한하고, 질의 문장을 임베딩해 Chroma로 검색한다.
테이블별·임베딩 모델별 collection을 구분하므로 모델 교체 시 다른 차원의 벡터를 섞지 않는다.
색인이 아직 없으면 indexing, 의존성·Ollama 오류이면 unavailable로 반환한다.
검색 요청은 미완료 색인의 갱신을 예약할 수 있으며, 일시적으로 최신 라운드가 빠질 수 있다.
오류 상태에서도 개인 메모와 게임 진행은 사용할 수 있다.

설계 시 확인한 공식 계약:
[Ollama embed](https://docs.ollama.com/api/embed),
[Chroma 데이터 추가](https://docs.trychroma.com/docs/collections/add-data),
[Chroma 검색](https://docs.trychroma.com/docs/querying-collections/query-and-get).

**환경과 실행**

기본 저장 경로는 `agents/data/`이며 `.gitignore`에 추가했다.
서버와 Table은 같은 데이터 루트를 사용해야 한다. subprocess에도 적용하려면
POKER_DATA_DIR 환경변수를 설정한다. 코드의 Table config만 바꿔서는 subprocess 설정이 바뀌지 않는다.

```sh
.venv/bin/python -m agents.demo
.venv/bin/python -m pip install -r agents/requirements-rag.txt
ollama pull embeddinggemma
```

Chroma는 선택 의존성으로 분리했으며 이번 작업에서 설치하거나 모델을 내려받지는 않았다.
실제 임베딩 모델·주소·timeout은 `GameConfig`에서 변경한다.
MCP subprocess 설정도 바꾸려면 해당 서버의 `GameConfig` 생성 지점에 같은 설정을 적용한다.
재시작 후 게임 자동 복구, 서버/UI, 참가자별 그래프 실행 스케줄링은 이번 범위에서 제외했다.

#### 검증 결과

- `.venv/bin/python -m unittest discover -s tests -v`: 기존 6개 포함 총 26개 테스트.
- 카드 순환·문양, 판돈 이월·좌석 회전, 대출·탈락·목표, 레이즈 후 재대응,
  중복 명령, 오래된 제안, 발언 보류·한도·일반 채팅, 컨텍스트·비공개 정보 검사.
- 개인 파일 격리·기억 수명, 초기 20판/FIFO, 색인 추가·중복 생략·퇴출은 mock 벡터 저장소로 검사.
- 기존 MCPClient로 실제 subprocess/stdin/stdout 연결 후 개인 메모 읽기·쓰기와 출처 바인딩 검사.
- 데모에서 LLM 없이 한 라운드 완료와 공개 기록 저장 확인.
- 최초 구현 시에는 실제 Chroma+Ollama 종단 간 검색을 검증하지 않았다. 아래 변경에서 검증했다.

### 사건별 청킹·Chroma 연동·메모 자동 첨부

이 항목은 위 최초 구현의 라운드당 단일 문서·발언 240자 제한·공통 메모 한도를 대체한다.
LangGraph 노드는 계속 사용자 작성 영역으로 두며, 기존 서버와 Ollama HTTP 구현은 변경하지 않았다.

#### 설정

`agents/config.py`에 다음 값을 둔다. 메모 한도의 단위는 토큰이 아닌 **문자 수**다.

| 설정 | 기본값 | 역할 |
|---|---|---|
| `ollama_model` | `qwen3:8b` | 게임 판단/채팅 모델. 새 `PlayerLLM`이 기존 Ollama 클라이언트에 전달 |
| `ollama_usd_per_1k_tokens` | 0.0 | 기존 클라이언트에 전달할 로컬 모델의 가상 단가 |
| `round_memo_max_chars` | 1000 | 단기: 이번 라운드 판단 |
| `game_memo_max_chars` | 2000 | 중기: 이번 게임 판단 |
| `long_memo_max_chars` | 3000 | 장기: 상대별 대응 |
| `embedding_model` | `embeddinggemma:latest` | 임베딩 모델 |
| `embedding_context_tokens` | 2048 | Ollama 요청의 num_ctx와 청킹 안전 여유 설정 |
| `embedding_chunk_max_bytes` | 1536 | 사건 청크의 UTF-8 바이트 예산. 토큰 수와 같은 값은 아님 |
| `embedding_batch_size` | 16 | 한 번에 임베딩/저장하는 청크 수 |

`GameConfig.memo_limit(scope)`로 round/game/long별 한도를 읽는다.
쓰기에서는 한도 초과를 거절하고, 읽기에서는 기존의 긴 파일도 앞부분을 해당 한도까지만 반환한다.
`PlayerLLM`에서도 테이블 설정으로 다시 제한하므로 subprocess 설정이 달라도 입력 예산을 지킨다.
모델에게 요약을 자동 요청하지는 않으며, 메모 갱신·요약 판단은 사용자 그래프가 담당한다.

#### chunker.py

- `EventDocument`: 라운드 내 사건 하나와 그 전체 텍스트. 사건 내용 해시를 포함한 key를 제공한다.
- `Chunk`: 사건의 일부 본문. 검색용 문서와 사건/청크 메타데이터를 만든다.
- `EventChunker.events(record)`: 공개 사건을 사건별 문서로 변환한다. 일반 잡담은 제외하고
  게임 발언·베팅·공개 결과 등의 원문 필드는 보존한다. 240자 자르기는 제거했다.
- `split_text(text, max_bytes)`: 유니코드를 깨뜨리지 않고 바이트 예산 내에서 나눈다.
  가능하면 줄바꿈·공백 경계를 사용한다. 모든 조각의 본문을 연결하면 원문과 동일하다.
- `chunks(event)`: 사건 출처 헤더를 포함한 크기를 계산해 하나 이상의 청크를 만든다.
- `bisect(chunk)`: 실제 모델이 길이 초과를 반환한 청크를 추가로 이등분한다.

청크는 다른 사건과 섞지 않는다. 사건 하나가 길면 그 사건의 청크 1, 2, …로 분할한다.
메타데이터는 experience_id, source, round_id, event_id, event_type, player_id,
event_key, chunk_index(1부터), chunk_count, schema_version이다.
공개 결과도 하나의 사건으로 색인하므로 큰 결과 JSON 또한 분할할 수 있다.
검색 결과의 experience_id와 round_id로 같은 판의 다른 사건/공개 결과와 연결할 수 있다.

토크나이저 다운로드를 추가하지 않기 위해 1536바이트를 보수적인 초기 예산으로 사용한다.
이는 정확한 토큰 계산이 아니므로 최종 길이는 `truncate=False`인 Ollama 호출에서 검증한다.
실제 context 길이 초과 HTTP 400이 발생하면 `_embed_chunks()`가 문제 배치를 좁혀 해당
청크만 더 작게 분할한 뒤 다시 요청한다. 다른 HTTP/네트워크 오류를 길이 문제로 간주하지 않는다.
최소 조각도 실패하면 오류를 보고하며, 원문을 조용히 잘라서 성공 처리하지 않는다.

#### MCP 서버의 Chroma 변경

- 기존 라운드당 벡터 하나를 사건별 여러 청크로 변경했다.
- `index_records()`는 실제 저장된 청크 메타데이터를 읽고 사건별 완성 여부를 확인한다.
  chunk_count와 1..N 인덱스가 모두 있으면 다시 임베딩하지 않는다.
  일부 청크만 저장된 사건은 재시도에서 복구한다.
- 청크 ID에는 사건 key와 청크 번호를 포함한다. 새 청킹 스키마·모델·예산에 맞는 별도
  collection을 사용하므로 예전 라운드 단위 색인과 섞이지 않는다. 기존 collection은 삭제하지 않는다.
- 보관 단위는 여전히 최근 **20판**이다. 한 판에서 청크가 여러 개 생겨도 보관 판수는 줄지 않는다.
  FIFO에서 빠진 라운드의 모든 사건 청크를 색인에서 삭제한다.
- Chroma PersistentClient와 collection을 서버 객체에서 재사용하며 data/chroma 아래 저장한다.
  임베딩은 Ollama에서 만들고 Chroma에는 벡터를 명시적으로 전달한다.
- 검색은 현재 보관 중인 experience_id로 필터링하며, 청크 메타데이터를 결과에 포함한다.
  긴 질의도 분할해 검색하고 같은 결과는 가장 가까운 거리로 합친다. limit은 반환 청크 수다.
- `read_memories` MCP 도구를 추가했다. 동일 출처의 세 메모를 하나의 lock 안에서 함께 읽는다.
  기존 개별 `read_memo`와 `write_memo`도 유지한다.

#### player_llm.py — 매 호출의 기본 메모

`PlayerLLM(table, mcp, player_id, client=None)`는 사용자 그래프가 사용하는 게임 LLM 진입점이다.
`client`를 생략하면 config.ollama_model로 기존 `agent.ollama_client.OllamaLLM`을 생성한다.

```python
from agents.player_llm import PlayerLLM

# 기존 MCPClient에 poker 서버를 연결한 뒤 참가자별로 준비한다.
llm = PlayerLLM(table, mcp, "A")

# 노드 안에서는 매 호출 await한다. 메모 조회 여부를 모델에게 묻지 않는다.
text, usage = await llm.complete(system, user, schema=schema)
# 또는 await llm.stream(system, user, on_token=callback)
```

`complete()`와 `stream()`은 호출할 때마다 테이블의 출처 바인딩으로 read_memories를 실행하고,
장기 → 중기 → 단기 메모를 system 입력에 붙인다. 빈 메모도 '(없음)'으로 표시한다.
수정된 메모는 다음 호출에 바로 반영하며, 조회 중 라운드가 바뀌면 재입력을 요구한다.
메모 조회 실패를 빈 메모로 숨기지 않는다.
동기 complete는 asyncio.to_thread로 기존 통신 코드를 실행하여 다른 참가자를 막지 않는다.

자동 첨부는 이 진입점을 사용하는 모든 호출에 적용된다. 사용자 그래프의 분류·일반 채팅·
게임 판단 등 모든 LLM 노드를 이 객체에 연결해야 한다. 기존 OllamaLLM을 직접 호출하면
게임 메모는 자동 첨부되지 않는다. 그래프 자체나 기존 운영 지원 에이전트의 동작은 바꾸지 않았다.

#### 검증

- 전체 unittest 33개: 기존 게임·MCP 검사에 무손실 유니코드 청킹, 길이 초과 재분할,
  메모별 한도, 매 complete/stream의 메모 첨부·갱신 검사를 추가했다.
- 설치된 실제 Chroma 1.5.9에서 임시 DB를 생성하고 사건 청크 저장·검색·중복 생략·
  부분 저장 복구·라운드 단위 퇴출·새 클라이언트에서의 영속성을 확인했다.
- 로컬 Ollama의 실제 embeddinggemma:latest로 긴 한국어 사건을 9개 청크로 저장했다.
  두 라운드가 총 24개 청크로 색인되었고 실제 질의에서 ready 상태와 결과 3개,
  event_id/chunk_index/chunk_count 메타데이터를 확인했다.
- 실제 모델 검증 데이터는 임시 폴더에만 기록했다. 판단 모델을 호출하거나 그래프를 구현하지 않았다.

### 참가자 그래프 스켈레톤과 AI-only 실행기

#### 파일별 편집 지점

| 파일 | 역할 | 사용자가 바꿀 지점 |
|---|---|---|
| `agents/graph.py` | StateGraph의 상태·노드·edge 구성 | `build_graph()`의 노드 연결, 검증 분기, 추가 단계 |
| `agents/poker_llm.py` | 노드가 호출하는 게임 업무 구현 | `PokerLLM.route/research/chat/decide/remember`, `DecisionDraft` 스키마와 프롬프트 |
| `agents/runner.py` | 여러 참가자 그래프를 독립 실행하고 Table에 결과 적용 | `RoundRunner.run_round()`. 게임 규칙이나 프롬프트는 여기 넣지 않음 |
| `agents/demo.py` | CLI 조립과 공개 사건 출력 | fake/ollama 모드 선택, 인원·모델·라운드·기록 경로 지정 |

기존 메모 자동 첨부 `PlayerLLM`은 그대로 사용한다. `PokerLLM`은 그 위의 업무 계층이다.

```text
Runner → 참가자별 graph
                ↓
             PokerLLM       업무 프롬프트·조회 정책·판단 결과 구성
                ↓
             PlayerLLM      매 호출 round/game/long 개인 메모 첨부
                ↓
             OllamaLLM      재사용 HTTP/JSON 통신
```

#### 기본 그래프

```text
START → route ── 인간의 명시적 잡담 ───────────────┐
             └─ 게임 → research ─────────────────┤
                                                 ↓
                                               answer
                                                 ↓
                                               validate
                                               ↙      ↘
                                    오류 피드백/재시도   remember → END
                                           └→ answer
```

- `route_node`: 검증된 인간의 channel=chat 발언이 자신을 지목했으면 일반 채팅 경로.
  raw 자연어의 의미 분류를 추가할 위치를 TODO로 표시했다.
- `research_node`: 기본은 상대 게임 발언을 과거 경험에서 검색한다. 배분·차례 알림은 건너뛴다.
  검색 필요 판단과 질의 전략은 `PokerLLM.research()`에서 확장한다.
- `answer_node`: 일반 대화는 `chat()`, 게임은 `decide()` 호출.
  LLM에 라운드/사건 ID를 생성하게 하지 않고 관찰 정보에서 붙인다.
- `validate_node`: 최소 발언, 현재 발언/베팅 가능 여부, 출력 길이, 채팅 응답 대상 등을 검사.
  실패 시 피드백과 함께 다시 answer로 이동한다. `config.graph_max_attempts` 기본 3회 이후 실패 처리.
- `remember_node`: 짧은 판단 요약을 write_memo로 저장할 확장 지점.
  **기본 구현은 아무것도 쓰지 않고 기존 메모를 유지한다.** 메모 생성/요약 정책은 사용자 작업이다.

전략은 완성된 최적 플레이가 아닌 실행 가능한 기본 프롬프트다. 성격·블러핑·동맹·발언 의사·
베팅 전략을 바꿀 위치에 TODO를 표시했다. 그래프 입력은 observation/event/force_bet이고,
반환은 `{"decision": AgentDecision에 맞는 dict}`다.
`GraphInput/AgentState/GraphOutput`은 TypedDict, 입출력 내용은 기존 Pydantic 계약으로 검증한다.

#### 실행기와 테이블 연결

`RoundRunner(table, graphs, on_event=None)`에 참가자 ID → 컴파일된 그래프 매핑을 준다.
같은 그래프 정의를 참가자별 backend로 구성하며, 런타임 상태는 각각 독립적이다.

1. 라운드를 시작하고 사건을 필요한 참가자에게 전달한다.
2. 참가자별로 asyncio task를 시작한다. 같은 참가자의 작업은 동시에 하나만 실행한다.
3. 작업 중 도착한 사건은 참가자별 최신 대기 사건으로 모은다.
4. 먼저 끝난 결과부터 `Table.apply_decision()`에 적용한다. 전원 완료를 기다려 모으지 않는다.
5. 새로운 게임 발언·베팅은 필요한 참가자를 다시 깨운다. 침묵 선언끼리는 서로를 깨우지 않는다.
6. 오래된 제안에 rejudge가 반환되면 최신 관찰 정보로 다시 실행한다.
7. 연속 발언으로 보류된 문장을 재판단할 때는 옛 문장을 자동 공개하지 않고 새 판단으로 교체한다.
8. 진행 중 판단도 추가 발언 제안도 없으면 현재 베팅 차례 참가자에게 force_bet=True로 행동을 요구한다.
   시간 경과로 침묵이나 공개를 결정하지 않는다.
9. 그래프/통신 오류 참가자는 `fail_agent()`로 표시하고 기존 무료 체크/유료면 폴드 정책을 적용한다.
10. 공개되면 남은 작업을 취소·정리한다. 선택적으로 종료 기록을 MCP에 전달한다.

`runner_max_decisions` 기본 120은 디버그 실행 상한이다. 무한 레이즈/재판단 같은 정책 오류에서
승패를 조작하거나 전체 참가자를 강제 폴드시키지 않고 실행 오류를 보고한다.
현재 runner는 AI-only이며 인간 입력 대기·일반 잡담 스케줄링·서버/UI는 아직 연결하지 않았다.
일반 채팅 그래프 경로 자체는 직접 호출하여 검증했다.

`Observation`에는 required_game_utterances와 last_speaker를 추가해 그래프가 엔진 내부 상태를
뒤지지 않아도 된다. 이미 침묵 상태인 참가자의 반복 선언은 새 사건을 만들지 않는다.
`Table.discard_deferred()`는 새 판단으로 보류 발언을 교체하는 연결점이다.

#### 실행 방법

```sh
# 외부 모델·MCP 없이 실제 StateGraph와 runner 연결을 확인
.venv/bin/python -m agents.demo --mode fake --seed 7

# config.ollama_model(현재 qwen3:8b)로 실제 AI 플레이
.venv/bin/python -m agents.demo --mode ollama --players 3 --rounds 1

# 모델 교체 및 종료 경험 색인 예약
.venv/bin/python -m agents.demo --mode ollama --model qwen3:8b --publish-memory
```

fake는 고정 정책이며 응답 품질 검증용이 아니다. 기본 참가자별 최소 1회 발언 후 체크/콜한다.
공개 발언·베팅·실패·카드 결과는 진행 중 출력하고, 각 라운드 원문은 기존처럼 저장한다.
종료 경험을 MCP로 보내려면 `--publish-memory`를 사용한다. 이를 생략해도 원문 기록은 저장된다.

#### _old 재사용과 설정 전달

사용자가 보관한 `_old/agent/`에서 `ollama_client.py`, `mcp_client.py` 두 파일만
활성 `agent/`로 복사해 재사용했다. 기존 업무 코드와 옛 서버는 계속 `_old`에 둔다.
옛 Ollama 회귀 테스트는 `_old.agent.*` 경로를 참조하도록 조정하여 테스트 수집을 유지했다.
MCPClient 기본 ops/docs 매핑은 바꾸지 않고, 데모에서 poker 서버 경로를 명시적으로 주입한다.

`export_mcp_config()`와 `load_mcp_config()`는 기존 MCP subprocess의 환경 상속을 이용해
동일한 GameConfig를 전달한다. 데모의 data-dir/메모 한도/임베딩 모델 변경이 subprocess에도
반영된다. 외부 서버에서 조립할 때도 같은 방식을 쓰거나 설정을 직접 주입하면 된다.

#### 검증

- 전체 unittest 41개 통과.
- 느린 A의 판단을 대기시키고 B의 공개 발언 이후에만 A를 풀어주는 테스트로,
  다른 참가자를 기다리는 일괄 응답 구조가 아님을 확인했다.
- 최소 발언/형식 오류의 제한된 재시도, 인간 일반 채팅 분기, 그래프 실패 시 라운드 진행,
  다음 라운드 좌석 회전, 무한 레이즈 디버그 상한과 작업 정리를 검사했다.
- fake 데모에서 AI 3명의 독립 그래프가 발언·체크 후 한 라운드 완료함을 확인했다.


실모델 검증 중 첫 발언의 text를 생략하는 출력을 발견하여 `DecisionDraft`의
speech/text/bet/wait_for_dialogue를 모두 필수로 만들었다. 관찰 정보에 따라
최소 발언이 남으면 speech=speak, 발언 불가면 빈 text, 내 차례가 아니면 bet=null,
강제 베팅 차례면 bet 필수 및 허용 action 목록으로 JSON 스키마를 좁힌다.
코드 검증도 유지하며 스키마만으로 업무 규칙을 전부 보장한다고 가정하지 않는다.

디버그 생성 제어를 config에 추가했다.

- `ollama_think=False`: 게임에서는 기본적으로 별도 thinking 생성을 끈다.
  True로 켜거나 None으로 모델 기본 동작을 사용할 수 있다.
- `ollama_num_predict=512`: 한 호출의 최대 생성 토큰 수. 긴 답변/추론 실험 시 조절한다.

활성 `agent/ollama_client.py`는 기존 통신 로직에 선택 인자 think/num_predict 전달만 추가했다.
인자를 생략한 호출은 이전 payload를 유지한다. `PlayerLLM`이 게임 설정을 전달한다.
관련 API 계약은 [Ollama chat](https://docs.ollama.com/api/chat)을 참고했다.

실제 로컬 qwen3:8b + MCP로 AI 두 명이 발언·체크·공개까지 완료했다.
추론 실패 참가자 없이 10회 판단 실행으로 한 라운드를 마쳤다. 실행 데이터는 임시 경로에만
저장했다. 이 검증은 기본 배선·출력 계약의 확인이며 블러핑/대화 전략의 품질 보장은 아니다.

### 연습용 분리와 실행용 그래프 완성

#### 연습용 두 파일

- `agents/graph_practice.py`: 상태 계약과 route/research/answer/validate/remember 노드,
  build_graph 시그니처 및 TODO만 제공한다. 노드 구현과 edge 연결은 비어 있다.
- `agents/poker_llm_practice.py`: `PokerLLMPractice`의 의존성 보관과 업무 메서드 시그니처,
  입력·출력 설명만 제공한다. route/research/chat/decide/remember/summarize_round를 직접 작성한다.

두 파일은 완성본 graph/poker_llm을 import하거나 상속하지 않는다. 구현되지 않은 메서드는
NotImplementedError를 명시적으로 발생시키며 모델 생성·호출·MCP 요청을 하지 않는다.
기본 demo도 이 파일들을 자동으로 사용하지 않는다. 연습이 끝난 후 본인의 조립 코드에서
practice의 build_graph와 PokerLLMPractice를 함께 연결하면 된다.

추천 연습 순서는 route → decide → graph 연결 → validate 재시도 → chat → research → 기억 갱신이다.
공통 Pydantic 계약과 기존 PlayerLLM/MCP/Table은 그대로 재사용할 수 있다.

#### 실행용 완성본

`agents/graph.py`와 `agents/poker_llm.py`는 실행용으로 유지한다.
기존 일반 채팅·게임 판단 외에 summary 경로와 실제 메모 저장을 추가하여 빈 remember를 채웠다.

```text
route
  ├─ chat    → answer(chat) ───────────┐
  ├─ game    → research → answer(decide) ┤→ validate → remember → END
  └─ summary → answer(summarize_round) ┘       └─ 제한된 재시도 → answer
```

- route: 공개가 끝나면 summary, 인간이 명시적으로 지목한 channel=chat 요청은 chat,
  나머지는 game이다. 아직 없는 HTTP/UI의 raw 발언 분류까지 대신 구현하지 않는다.
- research: 최신 상대 게임 발언과 현재 베팅액으로 검색한다. Rejudge/BetTurn 사건에서도
  관찰 정보의 최근 상대 발언을 활용한다. 같은 라운드·발언·베팅액의 ready 결과는 재사용하고,
  indexing/unavailable 결과는 영구 캐시하지 않는다.
- decide: 공개 행동 제안과 개인 메모 교체값을 별도로 생성한다. 발언·베팅 스키마는
  현재 관찰 정보로 제한하고 개인 메모에도 scope별 길이 안내와 코드 검증을 적용한다.
- summarize_round: 실제 공개 결과를 보고 단기 판단을 정정하고 중기 운영·장기 상대 대응을
  회고한다. 새 발언이나 베팅을 반환하지 않으며 폴드 카드를 입력에 추가하지 않는다.
- remember: 비어 있지 않은 메모 교체값만 기존 write_memo MCP 도구로 저장한다.
  빈 문자열은 기존 메모 유지다. 라운드가 바뀌었거나 한도를 넘으면 저장하지 않고 오류를 낸다.

`agents/models.py`의 `MemoryUpdate`는 round/game/long 문자열을 묶는다.
`AgentDecision.memory_updates`는 개인 데이터이며 공개 발언 text와 별개다.
Table은 행동과 공개 text만 적용하므로 메모는 공개 사건·라운드 JSON·RAG 경험에 포함되지 않는다.
UI에는 그래프 출력 전체 대신 기존 Table 공개 이벤트를 전달해야 한다.

게임 중 메모는 아직 승인 전인 **잠정 판단**으로 작성한다. 이후 공개 회고가 실제 결과를 반영한다.
메모는 LLM이 생성한 요약이므로 완벽한 상대 성향이나 최적 전략을 보장하는 자료는 아니다.
모든 새 LLM 호출에는 기존 PlayerLLM을 통해 세 메모가 계속 자동 첨부된다.

Runner는 공개 후 진행 중인 게임 판단을 먼저 취소·정리하고, 실패하지 않은 해당 라운드
참가자 모두의 summary 그래프를 실행한다. 폴드·탈락 참가자도 자신이 볼 수 있는 공개 결과로
회고할 수 있다. 회고는 공개 행동을 적용하지 않는다. 회고 오류는 참가자 실패 상태와 errors에
기록하지만 이미 확정된 승패·판돈은 바꾸지 않는다. 게임 중 판단과 공개 후 회고는 별개 단계다.

기본 fake 데모의 ScriptedPokerLLM은 모델을 사용하지 않으므로 회고에서도 빈 메모 제안만 반환한다.
실제 회고·개인 메모 생성 검증에는 ollama 모드를 사용한다.

#### _old 참조 사전 확인

실행용 `agent/`, `agents/`에는 `_old`를 import하거나 실행하는 코드가 없다.
`agent/__init__.py`의 `_old` 언급은 설명 문자열이다.

실제 참조는 **`tests/test_ollama_refactor.py` 한 파일**에 있다.

| 참조 | 용도 | 활성 경로로 변경하려면 |
|---|---|---|
| `_old.agent.incident_llm` | 이전 운영 업무 계약 회귀 검사 | `_old/agent/incident_llm.py`를 `agent/incident_llm.py`로 꺼내야 함 |
| `_old.agent.ollama_llm` | 이전 조립 생성자 검사 | `_old/agent/ollama_llm.py`를 `agent/ollama_llm.py`로 꺼내야 함 |
| `_old.agent.ollama_client` 및 해당 patch 경로 | 보관된 통신 코드 검사 | 활성 `agent/ollama_client.py`가 이미 있으므로 import/patch 경로를 바꾸면 됨 |

위 보관용 테스트를 유지하는 동안에는 해당 _old 파일도 테스트 의존성이다.
현재 게임을 실행하기 위해 추가로 꺼낼 파일은 없다. 사용자의 사전 통지 요청에 따라
이 결과를 먼저 알렸으며, **이번 작업에서는 _old 파일을 이동·복사하지 않았다.**
테스트까지 _old와 분리하려면 위 두 업무 파일을 꺼내고 테스트 참조를 바꾸거나,
보관 코드에 해당하는 테스트도 함께 보관 영역으로 옮기는 방법이 있다.

#### 검증

- 전체 unittest 44개 통과.
- 연습용 build_graph와 업무 메서드가 실제 모델을 호출하거나 완성본으로 위임하지 않음을 검사.
- 실행용 그래프가 개인 메모를 저장하고 공개 후 결과로 갱신하는 전체 흐름 검사.
- 다른 참가자의 개인 메모가 프롬프트에 섞이지 않고 개인 메모가 공개 기록에 남지 않음을 검사.
- 폴드한 참가자도 회고하되 해당 카드가 공개 결과에 노출되지 않음을 검사.

실제 Ollama 연동에서 큰 maxLength(예: 3000/4000)가 포함된 JSON 스키마를 전달하면
현재 설치된 서버에서 `failed to parse grammar` HTTP 400이 발생함을 확인했다.
출력 문법에서는 큰 문자열 반복 제약을 빼고 길이 설명을 제공하며, 실제 상한은 그래프 검증과
MCP write_memo에서 적용한다. 입력 메모 읽기의 scope별 상한도 그대로 유지한다.
또한 speak/quiet와 bet/no-bet의 유효 조합을 anyOf로 구성하여 silent+비어 있지 않은 text,
bet+wait_for_dialogue=true를 문법 단계에서도 차단한다. 검증·재시도는 계속 유지한다.

실제 qwen3:8b 기능 검증에서는 새 임시 테이블에서 첫 판단을 생성·승인하고,
베팅을 고정 체크로 진행해 공개한 뒤 회고 그래프를 실행했다. 단기/중기/장기 메모가 각각
106/118/240자로 저장되고, 공개 기록에 개인 메모가 포함되지 않음을 확인했다.
이는 모델 판단·MCP 저장·공개 회고 연결의 검사이며 자율 베팅 전략의 성공률 검사는 아니다.
별도의 자율 실행에서는 반복 맞레이즈로 runner_max_decisions 상한에 도달했다.
게임 규칙이나 승패를 임의로 바꾸지 않았고, 해당 실행이 오류로 종료·정리되는 것을 확인했다.
기본 전략의 반복성/공격성은 추가 튜닝 대상이며 코드의 출력 계약·오류 처리와 구분한다.

### 대출 제거와 이름 기반 레이즈

앞서 기록한 임시 대출·상환 및 자유 숫자 레이즈 규칙을 폐기한다. 대출은 없고,
LLM과 인간의 베팅 입력은 다음처럼 크기 이름만 지정한다.

```python
Bet(action='raise', raise_size='maximum')
# maximum=최대, large=큰(4), medium=중간(2), small=작은(1)
Bet(action='call')  # check/call/fold에는 raise_size 없음(null)
```

#### 한도 계산

`Betting.raise_options(pid)`가 현재 차례의 유효한 크기와 실제 증가량을 계산한다.
모든 숫자는 현재 최고 베팅액 위에 추가하는 **증가량**이다. 콜 비용은 별도로 낸다.

```text
각 참가자의 추가 여력 = 남은 칩 - 아직 내야 하는 콜 금액
최대 레이즈 증가량 = 폴드하지 않은 참가자들의 추가 여력 중 최솟값
```

이미 낸 베팅액이 서로 다르므로 단순히 잔액의 최솟값만 쓰면 뒤 참가자가 콜조차
못 하는 상황이 생긴다. 아직 내야 하는 콜을 먼저 제외하여 모두 따라올 수 있게 한다.
예를 들어 잔액 6개에 콜 비용 4개가 남은 사람이 있으면 최대 추가 레이즈는 2개다.
이때 `maximum=2`, `medium=2`, `small=1`만 허용하고 `large`는 제공하지 않는다.
같은 증가량의 이름은 유지한다. 한도가 0이면 check/call/fold 중 가능한 행동만 제공한다.
폴드한 사람은 한도 계산에서 제외하고, 다음 행동 시 다시 계산한다.

#### 파일 연결점

- `models.py`: `Bet.amount`를 제거하고 `raise_size` 이름 계약으로 교체.
  raise에만 이름을 요구한다. `Player.debt` 제거. `Observation.raise_options` 추가.
- `betting.py`: `raise_options()`에서 계산, `act()`에서 최신 한도 재검사 및 금액 적용.
  사용할 수 없는 크기는 상태 변경 전에 거절한다. `_pay()`는 부족한 칩을 빌리지 않는다.
  참가비도 전원이 낼 수 있는지 확인한 후 차감하여 중간 실패 시 일부만 차감하지 않는다.
- `table.py`: 관찰에 선택지를 제공하고 공개 `BetPlaced`에 선택한 `raise_size` 및 엔진이
  확정한 숫자 `amount`를 함께 기록한다. 기존 멱등 처리와 오래된 판단 거절을 유지한다.
- `poker_llm.py`: 최대/큰/중간/작은 의미를 프롬프트에 명시하고, 출력 스키마에서도
  현재 가능한 행동과 크기만 허용한다. 모델은 콜 비용이나 최대 증가량을 계산하지 않는다.
- `graph.py`: 관찰에 없는 크기를 거부하고 기존 제한 횟수 내 재판단한다.
- `round.py`, `config.py`: 대출 상환 제거. 초기 칩은 참가비 이상이어야 한다.
  다음 참가비를 못 내는 참가자는 라운드 정산 후 탈락한다(기본 참가비 2이면 잔액 0/1).
  남은 1개를 없애거나 판돈에 편입하지 않고 그대로 보존한다.
- `mcp_server.py`: `_public_record()`의 `BetPlaced` 허용 필드에 `raise_size`를 추가한다.
  기존 숫자 `amount`도 유지해 실제 증가량을 검색할 수 있다. 임베딩·청킹·메모 도구는 유지한다.
- `seed_rounds.json`: 기존 대화와 카드/폴드 결과를 유지하면서 이름 기반 크기로 베팅을
  다시 실행하고 판돈·기여액을 재계산한 공개 기록 20개로 갱신한다.

연습용 두 파일은 미구현 상태 그대로 유지한다. server/UI 및 `_old` 파일 이동은 없다.

#### 검증

- 전체 unittest 48개 통과. 콜 비용을 고려한 상한, 허용하지 않는 크기의 무변경 거부,
  가장 적은 칩의 참가자 폴드 후 한도 증가, 대출 없는 정산과 참가비 부족 탈락을 검사했다.
- 세 에이전트가 최대 레이즈를 계속 선택하려 해도 첫 최대 레이즈 이후 추가 레이즈가
  사라지고 콜/공개로 종료됨을 실행기와 그래프를 연결하여 확인했다.
- 실제 로컬 `qwen3:8b`가 새 행동/크기 JSON 스키마를 처리하고 유효한 체크를 반환했다.
  이는 스키마 호환 검사이며 장기간 자율 플레이의 전략 품질 검사는 아니다.

### 인간 입력과 복합 발언 처리

인간과 AI의 게임 규칙은 동일하게 유지한다. 명령을 만드는 주체만 다르다.
AI는 참가자별 그래프가 판단을 만들고, 인간은 서버가 확인한 참가자 ID와
`PlayerCommand`를 실행기에 제출한다. HTTP/UI는 이번 변경에 포함하지 않는다.

#### 실행기와 인간 입력

`agents/runner.py`의 `RoundRunner`가 인간 명령과 AI 완료 결과를 한 `asyncio.Queue`에서
접수 순서대로 처리한다. 같은 참가자의 그래프는 한 번에 하나만 실행하고,
다른 참가자들의 판단 작업은 독립적으로 계속 실행한다. 인간 명령도 최종적으로
`Table.submit()`에 전달하므로 베팅 순서·금액·발언 제한·중복 방지 규칙을 공유한다.

서버에서 사용할 연결점:

- `run_round(...)`: 백그라운드 task로 실행하고 완료 시 공개 결과를 반환한다.
  이미 실행 중이면 두 번째 실행을 거부한다. 인간에게 그래프를 요구하지 않는다.
- `started`: 라운드 시작/재개 및 최초 사건 전달이 끝나면 설정되는 `asyncio.Event`.
  서버는 시작 task의 예외도 함께 감시해야 한다.
- `submit_human(player_id, command)`: 명령을 큐에 넣고 `CommandResult`를 기다린다.
  `player_id`는 웹 본문을 그대로 믿지 말고 서버의 참가자 연결에서 지정한다.
  `accepted/rejected/deferred`, 사건 번호, 재판단 여부를 UI에 돌려주면 된다.
- `release_human_speech(player_id, round_id, basis_event)`: 보류된 연속 발언을
  인간이 최신 상황에서 다시 확인해 공개할 때 사용한다. 자동 공개하지 않는다.
- `on_event(event)`: 확정된 공개 사건을 받는 동기 콜백. 서버가 구독자 큐에 전달한다.
- `running`, `errors`, `interpretation_errors`: 실행 상태, AI 실패, 인간 발언 해석 실패 확인.
- 실행 task 취소: 남은 그래프를 정리하고 대기 중인 인간 요청에도 거절 결과를 반환한다.
  임의로 승패를 만들거나 인간을 자동 폴드시키지 않는다.

인간의 최소 게임 발언 또는 베팅 입력을 기다리는 것은 정상 상태다.
시간 경과를 침묵이나 자동 폴드로 해석하지 않는다. 발언 없음 선언도 베팅과 별개다.
공개 이후 새 입력은 거부한다. 요청 ID 중복은 같은 결과를 반환하고,
같은 ID에 다른 내용·지난 라운드 명령·오래된 베팅은 거부한다.
재접속/서버 재시작 복원이나 다중 프로세스 간 테이블 공유는 추가하지 않았다.
현재 실행기와 명령 제출은 같은 이벤트 루프에서 사용한다.

#### 복합 발언을 한 번 해석

새 `agents/speech_interpreter.py`의 `SpeechInterpreter.interpret()`가 인간의 발언을
참가자별로 여러 번 분류하지 않고 접수 시 한 번 해석한다. 기존
`agent.ollama_client.OllamaLLM`을 그대로 사용한다. 개인 메모·카드·MCP 도구는
해석 모델에 전달하지 않고 공개 원문, 원문 구절, AI ID 목록과 명시 대상만 전달한다.

`SpeechInterpretation`은 여러 `SpeechIntent`를 담는다.

- `game`: 카드, 베팅, 동맹, 협상, 미래 보상 약속.
- `chat`: 날씨, 인사 등 일반 대화.
- `private_request`: 시스템 프롬프트, 개인 메모, 비공개 정보 요청.

각 의도에는 `targets`와 원문 구절 `text`가 있다. 원문을 별도 발언으로 나누어
공개하거나 횟수를 여러 번 차감하지 않는다. 문장부호/접속사 기준의 후보 구절은
코드가 보존하고, 모델의 출력 문법에서는 실제 구절과 AI ID만 선택하게 한다.
의미 분류와 호칭의 연결은 모델이 판단한다. 원문에 없는 인용, 존재하지 않는 대상,
대상이 없는 일반 질문은 검증에서 거부한다. 형식 오류는 제한 횟수만 재시도한다.
해석 자체가 실패하면 게임 상태를 바꾸지 않고 해당 요청을 거부한다.

설정은 `GameConfig`의 `router_model='qwen3.5:2b'`, `router_num_predict=768`,
`router_max_attempts=2`, `router_timeout=30.0`으로 관리한다. timeout은 시도별 상한이다.
실제 시험에서 `qwen3:0.6b`는 복합 문장 전체를 game으로 뭉치는 경우가 있어
기본값을 2b로 잡았다. 모델명은 교체할 수 있다. 참가자 판단 모델 설정은 별개다.

#### 참가자별 전달과 응답

원문과 공유 해석을 공개 `Speech` 사건과 `Conversation`의 최근 대화 기록에 붙인다.
게임 내용이 하나라도 있으면 인간 발언 전체를 game으로 기록하여 최소 게임 발언에도
포함한다. 잡담/비공개 요청만 있으면 chat이다. 인간의 발언 횟수는 어느 경우든 1회다.

게임 발언은 발언자를 제외한 모든 활동 중 AI에게 전달한다. A/C에게 B 견제를
제안해도 B까지 듣고 대응한다. 잡담·비공개 정보 요청은 해석에서 지정한 AI에게
응답을 요청한다. 여러 대상이면 각자가 한 번씩 답할 수 있다.

`RoundRunner`는 일반 게임 재판단의 계기는 최신 사건으로 합치되, 인간의 질문은
참가자별 보관함에 순서대로 남긴다. 생각하는 동안 다음 질문이나 베팅 사건이 와도
질문을 덮어쓰지 않는다. 다음 그래프 실행은 최신 `Table.observe()`를 받으므로
현재 라운드의 최근 대화가 함께 들어간다. 자기 연속 답변은 다른 발언이 생길 때까지
보관하고, 베팅 판단은 별도로 진행할 수 있다. 최소 게임 발언도 일반 답변에 묻혀
빠지지 않도록 실행 우선순위와 mixed 검증에서 확인한다.

`poker_llm.py`와 `graph.py`의 경로는 summary/chat/game/mixed다.
라우터 자체는 공유 해석을 읽어 경로를 고르는 코드이고, 의미 해석은 이미 앞단의
작은 LLM이 수행했다. mixed 경로는 원문·전체 대화·해석·게임 상태를 보고 응답한다.

- 게임 내용도 답하면 `response_channel=mixed`: 공개 발언은 하나, 게임 발언 1회 차감.
- 일반 질문에만 답하면 `response_channel=chat`: 게임 내용과 베팅을 넣지 않고
  AI 게임 발언 횟수는 차감하지 않는다. 게임 발언을 소진했어도 이 답변은 가능하다.
- 실제 반환 계약은 `AgentDecision.channel`과 서버가 붙이는 `target/reply_to`다.
  모델은 요청 ID나 인간 참가자 ID를 만들어내지 않는다.

개인 프롬프트/메모 공개 요청은 거절하고 날씨 확인 도구가 없음을 알리도록 프롬프트에
명시했다. 상대의 미래 보상 약속은 공개 주장으로 참고할 뿐, 칩 이동이나 미래 베팅을
예약하는 명령으로 실행하지 않는다. 실제 금액과 승패는 계속 엔진만 결정한다.

#### 기존 인프라와 저장

- 참가자 호출: `PokerLLM → PlayerLLM → agent/ollama_client.py` 유지.
  참가자의 실제 호출에는 기존처럼 세 종류 개인 메모를 붙인다.
- 도구 호출: `Table.call_player_tool() → agent/mcp_client.py` 유지.
- `mcp_server.py`: 공개 Speech의 `interpretation` 필드를 허용하되,
  Pydantic 계약·원문 인용·대상 ID를 재검사한다. 별도 도구나 DB는 추가하지 않았다.
- `chunker.py`: mixed 응답도 게임 내용이므로 사건 단위 경험 색인에 포함한다.
  일반 chat은 기존처럼 경험 색인에서 제외한다.
- 연습용 graph/poker_llm 파일, 기존 두 클라이언트, `_old` 서버/웹 파일은 그대로 둔다.

서버 조립 예시(기존 MCPClient를 poker 서버에 연결하고 설정 환경을 전달한 상태):

```python
from agents.player_llm import PlayerLLM
from agents.poker_llm import PokerLLM
from agents.graph import build_graph
from agents.runner import RoundRunner
from agents.models import PlayerCommand

# table = Table(['A', 'B', 'C'], human_id='human', config=config)
graphs = {
    pid: build_graph(PokerLLM(table, mcp, pid, PlayerLLM(table, mcp, pid)))
    for pid, player in table.players.items() if player.kind == 'ai'
}
runner = RoundRunner(table, graphs, on_event=publish_public_event)
round_task = asyncio.create_task(runner.run_round(mcp=mcp))
await runner.started.wait()

view = table.observe('human')
result = await runner.submit_human('human', PlayerCommand(
    request_id='unique-request-id', round_id=view.round_id,
    basis_event=view.event_id, kind='speak',
    text='B야 날씨 좋지? C야 이번에는 A를 견제하자.',
))
# 베팅은 같은 메서드에 kind='bet', bet=Bet(...)로 제출한다.
# 명시적 발언 없음은 kind='silent'. 이후 round_task가 공개 결과를 반환한다.
```

#### 검증 및 모델 품질의 범위

`tests/test_poker_human.py`에서 외부 모델 없이 다음을 검사한다.
인간 입력 대기 및 한 라운드 완료, AI 생각 중 인간 입력, 공유 해석 1회와 전체 전달,
복합 답변 횟수 계산, 게임 발언 소진 후 일반 답변, 다중 수신자의 답변 권한,
질문 보관과 최신 컨텍스트, 인간 보류 발언 재확인, 중복/지난 라운드/오래된 베팅 거절,
해석 보정 재시도, 실패 시 무변경 거부, 종료/취소 시 대기 요청 정리.

로컬 실제 모델에서도 qwen3.5:2b가 날씨/chat, 프롬프트/private_request,
견제 제안/game을 서로 다른 대상으로 분리하고 qwen3:8b의 mixed 응답까지
연결됨을 확인했다. 이 검사는 스키마와 전달 경로 검사다. 실제 응답에서 남의 약속을
자기 약속처럼 말하는 주체 혼동도 관찰했다. 원문·발언자·본인 ID와 약속 주체 지침을
전달하지만 자연어 판단 품질은 모델에 의존한다. 그런 문장이 실제 칩 이동을 일으키지는 않는다.

### 서버 연결 아키텍처와 HTTP 계약

`server.py`의 FastAPI, lifespan, app.state 공유, StreamingResponse/SSE, 종료 시
트레이스 flush 틀을 재사용한다. 이전 업무의 SupervisorDeps/fast-quality 모델 조립과
질문 하나에 답변 하나를 반환하는 chat API는 포커 테이블 API로 교체한다.
웹 서버와 HTML은 다음 단계에서 이 API를 중계·사용한다.

```text
web_chat.html → web_demo_server.py(동일 출처 프록시) → server.py
                                                     ↓
                                      agents/game_service.py
                                      GameService → GameSession
                                                     ↓
                                      Table + RoundRunner + 참가자별 그래프
                                                     ↓
                           기존 MCPClient / PlayerLLM → 기존 OllamaLLM
```

서버 프로세스는 GameService와 MCP 연결을 한 번 만든다. 게임마다 Table, 참가자별
그래프, RoundRunner, 백그라운드 라운드 task, 공개 사건 버퍼를 가진다.
실제 조립/작업 정리는 agents 안의 서비스가 맡고 HTTP 함수는 호출을 중계한다.
라운드는 명시적으로 시작한다. 공개 후 회고/저장까지 끝나면 다음 라운드를 시작할 수 있다.
SSE 연결 자체는 게임 실행을 시작하거나 중지하지 않는다.

| 메서드 | 경로 | 역할 |
|---|---|---|
| GET | `/health` | 프로세스 상태, backend, 모델, 활성 게임 수 |
| POST | `/games` | AI 2명 이상과 선택적 인간을 둔 테이블 생성 |
| GET | `/games/{game_id}` | 현재 단계·실행 상태·해당 인간의 관찰·다음 입력 선택지 |
| POST | `/games/{game_id}/rounds` | `{round_id: 1}`처럼 목표 라운드 번호로 시작 |
| POST | `/games/{game_id}/commands` | 기존 PlayerCommand로 발언/발언 없음/베팅 제출 |
| POST | `/games/{game_id}/commands/release` | `{round_id, basis_event}`로 보류 발언 재확인 |
| GET | `/games/{game_id}/events` | `after` 또는 Last-Event-ID 이후의 공개 사건을 SSE 구독 |
| DELETE | `/games/{game_id}` | 라운드 작업 정리·구독 종료·메모리에서 테이블 제거 |

생성 본문은 `ai_ids`(기본 A/B/C), `human_id`(기본 human, null이면 AI-only)다.
게임 규칙과 모델 설정은 서버의 GameConfig에서 가져오며 클라이언트가 임의로 바꾸지 않는다.
생성 결과의 `access_token`은 이후 `Authorization: Bearer ...`로 전달한다.
이 토큰이 게임의 소유/인간 참가자 연결이다. 명령 본문에 actor/player_id나 개인 도구 인수를
받지 않으며 AI 관점 조회 API도 만들지 않는다. AI-only에서는 공개 전 카드가 없는 관전자
상태만 제공한다. 토큰은 생성 응답 외 상태/SSE/로그에 포함하지 않는다.

게임 생성과 라운드 시작을 분리하여 구독부터 연결할 수 있게 한다. 같은 round_id 시작
재요청은 새 카드를 배분하지 않는다. 실행/회고 중 다음 라운드 시작은 409로 거절한다.
명령의 게임 규칙상 거절은 CommandResult의 rejected/rejudge로 반환하고 최신 상태도
같이 제공한다. HTTP 401/403은 토큰, 404는 없는 게임, 409는 수명/진행 충돌,
422는 요청 형식 오류다. 보류 발언 공개도 별도 LLM 호출 없이 기존 해석을 유지한다.

SSE id는 전송 버퍼의 sequence이고, PlayerCommand.basis_event는 Table의 event_id다.
둘은 용도가 다르다. 서버 사건 RoundReady/RunFailed/GameClosed도 같은 SSE 순서에
들어가므로 sequence를 베팅의 basis_event로 사용하면 안 된다. 상태 조회의 stream_cursor로
이어 구독한다. 화면은 공개 사건을 표시하고 필요 시 상태를 다시 조회한다.
연결이 끊겨도 task는 유지하며 커서로 재연결한다. 버퍼 범위를 벗어난 커서는 상태 재조회를
요구한다. heartbeat는 통신 유지용일 뿐 게임의 침묵 판정이나 자동 폴드와 무관하다.

메모리 내 게임/연결 정보는 단일 프로세스에서 관리한다. 라운드 공개 기록과 개인 메모는
기존 저장 코드를 사용한다. 서버 재시작 복원이나 다중 worker 간 테이블 공유는 추가하지 않는다.
종료 시 게임 작업을 먼저 취소·정리하고 그다음 MCP를 닫는다. 회고/공개 기록 이후 RAG 전달
실패는 게임 결과를 바꾸지 않고 별도 상태로 알리며 다음 전달 시 미전송 기록을 재시도한다.

#### 서버 구현과 사용 순서

`server.py`는 API 요청 모델/인증/HTTP 오류/SSE 형식과 lifespan만 담당한다.
새 `agents/game_service.py`에 GameService(게임 목록과 공유 의존성),
GameSession(한 테이블의 task/관찰/시작·종료), EventJournal(제한된 전송 버퍼)을 둔다.
서버 출력에 개인 메모나 내부 예외 원문을 넣지 않는다. 구동 오류는 일반 오류 상태와
RunFailed 사건으로 알리고 상세 내용은 서버 로그에 남긴다.
기존 SqliteSessionStore는 이전 질의응답 이력용이므로 꺼내지 않았다. 게임의 공개 대화·결과는
이미 있는 Table 라운드 JSON 저장을 사용한다. 공개 전 카드까지 복원하는 재시작 기능은 별개다.

기본 실행은 외부 모델/MCP 없는 배선 검사 모드다.

```sh
.venv/bin/python server.py
# 실제 모델과 기존 MCPClient를 쓰는 실행:
LLM_BACKEND=ollama .venv/bin/python server.py
```

`http://127.0.0.1:8100/docs`에서 요청 모델을 확인할 수 있다.
실제 호출 모드는 GameConfig의 모델을 쓰며 OLLAMA_MODEL/ROUTER_MODEL 환경변수로도
교체할 수 있다. MCP는 poker 스크립트만 한 번 연결하고 설정을 subprocess에 전달한다.
모든 테이블이 이 연결을 재사용한다. fake는 ScriptedPokerLLM과
ScriptedSpeechInterpreter를 쓰며 의미 해석 품질을 시험하는 모드가 아니다.

웹의 호출 순서는 다음과 같다.

1. `POST /games`에 `{"ai_ids":["A","B","C"],"human_id":"human"}` 전달.
   응답 `{access_token, game}`에서 game.game_id와 토큰을 보관한다.
2. 이후 게임 API에 `Authorization: Bearer <access_token>`을 붙인다.
   fetch 기반 SSE를 사용하면 같은 헤더를 보낼 수 있다.
3. `GET /games/{id}`의 game 상태를 읽고 stream_cursor 이후 사건을 구독한다.
4. `POST /games/{id}/rounds`에 `{"round_id":1}` 전달. 기다리는 것은 시작까지이며
   전체 라운드가 끝날 때까지 HTTP 요청을 붙잡지 않는다.
5. 발언/발언 없음/베팅을 `/commands`로 보내고 `{result, game}`을 받는다.
   이 game에는 최신 관찰과 버튼에 쓸 allowed_bets/raise_options가 있다.
6. RoundEnded는 공개 시점, RoundReady는 회고/전달까지 끝난 다음 라운드 준비 시점이다.
   다음 시작 가능 여부는 can_start_next_round로 확인한다.
7. 나갈 때 DELETE. 브라우저의 SSE 연결 종료만으로는 테이블을 취소하지 않는다.

명령 예시(숫자는 마지막 상태 조회에서 가져온다):

```json
{"request_id":"speech-1","round_id":1,"basis_event":12,
 "kind":"speak","text":"B야 날씨 좋지? C야 A를 견제하자."}
```

```json
{"request_id":"bet-1","round_id":1,"basis_event":18,
 "kind":"bet","bet":{"action":"raise","raise_size":"medium"}}
```

발언 없음은 `kind="silent"`이며 text/bet을 넣지 않는다.
서버가 보관한 human_id가 actor다. request_id는 각 새 명령에 새 값을 쓰고,
같은 요청 재전송은 같은 ID와 본문을 유지한다. 실행기 거절/보류는 HTTP 200의
CommandResult로 확인한다. AI-only의 인간 입력은 HTTP 409다.
SSE의 sequence와 별개인 game.event_id/observation.event_id를 basis_event로 쓴다.

SSE 연결은 `/events?after=<stream_cursor>`, 재연결은 Last-Event-ID도 지원한다.
서버가 둘 중 큰 커서를 사용한다. 초기 조회부터 구독까지 생긴 사건도 버퍼에서 재생한다.
GameConfig.event_history_limit(기본 2000)을 벗어난 커서는 HTTP 409다.
이미 구독 중인 느린 클라이언트가 버퍼를 놓치면 ResyncRequired를 받고 상태를 다시 조회한다.
GameConfig.sse_heartbeat_seconds(기본 15초)의 주석 프레임은 게임 상태에 영향을 주지 않는다.

#### 서버 검증

- 기존 규칙/그래프/도구 테스트와 HTTP 수명/인증/카드 비공개/SSE 검사를 함께 실행한다.
- HTTP에서 인간 포함 생성 → 중복 시작 → 발언 → 침묵 → 체크 → 공개 → 다음 라운드 → 종료 확인.
- AI-only 관전자에게 공개 전 카드가 없고, 인간 조회에서 자기 카드가 숨겨짐을 확인.
- 다른 게임 토큰과 actor 사칭 거부, 내부 오류/메모 문자열과 토큰의 상태/SSE 미노출 확인.
- SSE 재생·다중 구독·heartbeat·커서 만료 및 라운드 사이 전송 순서 확인.
- 시작 실패와 종료 시 대기 작업 정리, 공유 MCP 1회 생성과 부분 연결 실패 정리 확인.
- 경험 전달 실패가 공개 결과를 바꾸지 않고 다음 라운드에서 미전송 기록을 재시도함을 확인.
- 실제 기존 MCPClient로 poker 서버에 연결하여 ollama 모드 테이블 생성과 종료까지 확인.
  이 검사는 MCP 조립 검사이며 HTTP를 통한 실모델 한 라운드 전체 검사는 아니다.

검증 결과: 전체 unittest 72개 통과. HTTP/서비스 전용 11개 테스트를 포함하며,
최종 HTTP 의존성은 같은 이벤트 루프에서 게임을 조회하도록 비동기로 연결했다.

### 웹 프록시 최소 연결

`web_demo_server.py`의 FastAPI 앱, `/` HTML 제공, 고정 8100 백엔드, 요청별 httpx 클라이언트
구조를 유지했다. 기존 chat 전용 중계 함수를 게임 API 공통 중계 함수 하나로 교체했다.
`GET /health`, `POST /games`, `/games/...`의 GET/POST/DELETE를 그대로 백엔드에 전달한다.
웹 HTML은 아직 게임 API를 호출하도록 바꾸지 않았다.

별도 로그인/인증 저장소는 추가하지 않았다. Authorization, Last-Event-ID, Content-Type,
Accept와 원래 본문·쿼리만 전달한다. 인증과 참가자 판단은 server.py가 그대로 맡는다.
JSON 응답의 상태 코드/본문을 유지하고, events의 성공 응답만 버퍼링 없이 SSE로 중계한다.
401/403/409 등은 원래 HTTP 오류로 반환한다. SSE 종료 시 upstream과 클라이언트를 정리한다.
백엔드 연결 실패는 502, 연결/일반 요청 timeout은 504로 반환한다.

프록시 전용 검사에서 게임 경로/본문/토큰/커서 전달, SSE 프레임과 연결 정리,
인증 오류 상태 유지, 백엔드 연결 실패를 확인했다.

### 웹 프록시 수명별 분리와 상태 점검

공통 중계 함수를 다음 세 함수로 나눴다. 인증 기능과 게임 규칙은 추가하지 않는다.

- `proxy_health()`: 프록시가 응답 가능하고 HTML 파일이 있는지 확인하고,
  백엔드 `/health` 결과도 함께 반환한다. 둘 다 정상일 때 200, 하나라도 이상이면 503.
  응답의 proxy/backend를 구분하므로 백엔드가 꺼져도 프록시 자체 상태를 확인할 수 있다.
- `proxy_games()`: 일반 게임 요청/응답만 중계하고 함수 안에서 HTTP 연결을 닫는다.
- `proxy_events()`: SSE 연결을 열고 사건을 계속 전달한 뒤 종료/연결 실패 시 정리한다.
  `/games/{game_id}/events`를 wildcard 게임 경로보다 먼저 등록한다.

백엔드 SSE는 `/games/{game_id}/events` 한 경로다. 확정된 Speech, BetPlaced,
RoundStarted/RoundEnded, PlayerSilent, AgentFailed, RoundReady, RunFailed,
MemoryPublishFailed, GameClosed 등을 전달한다. LLM의 생성 토큰 스트림은 노출하지 않는다.
일반 POST로 인간 명령을 접수하고, 이후 인간/AI의 확정 사건을 독립적인 SSE에서 받는다.
연결은 여러 라운드에 걸쳐 유지되며 접속 종료로 게임 task 자체를 중지하지 않는다.

#### 프록시를 두는 이유와 전달 흐름

브라우저는 8600의 HTML과 같은 출처로 게임 API를 호출한다. 웹 프록시가 8100의
백엔드로 전달하므로 브라우저에서 두 서버 사이의 CORS 설정을 따로 맞출 필요를 줄인다.
프록시는 게임 규칙이나 참가자 권한을 다시 판단하지 않고 요청/응답을 중계한다.
서버가 발급한 토큰과 재연결 커서를 전달하며 별도의 로그인이나 인증 저장소는 두지 않는다.

```text
브라우저 ── POST 명령 ──→ proxy_games() ──→ server.py ──→ RoundRunner/Table
브라우저 ←─ 접수 결과 ── proxy_games() ←── server.py

브라우저 ←─ 공개 사건 ── proxy_events() ←── server.py의 사건 구독
```

일반 요청은 백엔드 응답 전체를 받은 뒤 연결을 닫고 반환한다. SSE는 응답 객체를
반환한 이후에도 백엔드 연결을 유지하고 데이터를 전달하므로 별도 함수로 분리했다.
이렇게 하면 일반 요청의 함수 종료와 SSE의 연결 종료를 같은 finally에서 구분하지 않아도 된다.
`proxy_health()`는 단순 중계와 달리 자기 상태와 백엔드 상태를 합쳐야 하므로 별도로 둔다.

#### 사건 전달에 SSE를 사용하는 이유

인간이 명령을 제출하지 않는 동안에도 AI가 발언하거나 베팅하고 라운드가 끝날 수 있다.
브라우저가 계속 상태를 물어보지 않아도 새 소식을 받도록 서버→브라우저 통로를 열어 둔다.
인간→서버 입력은 기존 HTTP POST로 충분하므로 양방향 WebSocket 대신 SSE를 사용했다.
기존 코드에 SSE 중계가 있었던 점도 재사용한 이유다.

사건 자체를 SSE로 처리하는 것은 아니다. Table이 사건을 확정하면 내부 실행기가
다른 AI의 재판단에 사용하고 공개 기록에 저장하며, 서버는 같은 공개 사건을 SSE로 전달한다.
AI 간 내부 전달은 실행기/큐가 담당한다. SSE 구독이 없어도 게임은 실행된다.
명령 접수 응답과 그 이후의 사건 알림도 별개다. 예를 들어 체크 접수는 일반 HTTP 응답이고,
뒤이어 발생한 다른 참가자의 발언이나 카드 공개는 SSE로 받을 수 있다.

운영 주소는 백엔드 8100, 웹 프록시/HTML 8600이다. 웹 프록시의 역할 분리는 완료했고,
HTML의 게임 화면과 API 호출 연결은 다음 작업이다.
검증 결과: 상태 점검 정상/백엔드 장애/HTML 누락과 일반 중계·SSE 처리를 포함하여
전체 unittest 78개 통과. 이번 변경은 문서 정리만 수행했다.

### 데스크톱 Vue UI 구조와 명세

처음 만든 CDN용 JS 컴포넌트 스켈레톤은 정리하고, 사용자 요청에 따라
`web/`를 Vue 3 + Vite + JavaScript + script setup 기반 프로젝트로 구성했다.
패키지와 lockfile을 두고 `npm run dev/build/preview`를 제공한다.
기존 CDN `web_chat.html`은 `_old/web_chat.html`에 보관했다.

App.vue는 조립만 맡는다. GameScreen, GameSetup, GameToolbar와 table/controls/history
폴더의 각 .vue 컴포넌트로 나눈다. PlayerSeat가 SlimeAvatar/PlayingCard/SpeechBubble을
가져오며 각 컴포넌트의 고유 스타일은 같은 파일의 scoped style에 둔다.
HTTP/SSE 서비스, 실시간/리플레이 composable, 서버 응답→화면 데이터 adapter는 별도 모듈이다.
Router/Pinia/TypeScript는 이번 스켈레톤에 추가하지 않았다.

명세는 `web/UI_SPEC.md`, 실행/폴더 안내는 `web/README.md`, UI 데이터 계약은
`web/src/contracts.js`에 둔다. 기준은 넓은 데스크톱이다. 인간은 플레이/관전 모두
하단 human-anchor로 남고 관전자 앵커를 엔진 참가자로 추가하지 않는다.
AI 좌석/색은 게임 생성 시 고정하고 라운드별 베팅 시작 순서만 별도 반영한다.
캐릭터는 둥근 몸/흰 눈 두 개/눈의 작은 상하 움직임으로 계획한다. LLM 표정 출력은 후속이다.
카드/말풍선/베팅/칩은 좌석 안에서 각각 역할을 나눈다.

리플레이는 liveState와 표시 커서를 분리하고 완료 라운드/프레임을 탐색하는 계약이다.
정확한 과거 관찰 저장/조회 API는 아직 없으므로 선행 백엔드 작업으로 명시했다.
SSE 이후 현재 상태를 찍어 과거 프레임으로 가장하지 않는다. 숨긴 카드/개인 메모는
프레임에 넣지 않고 나중에 공개된 카드도 과거 시점에 역으로 채우지 않는다.

개발 화면은 Vite 5173 → 기존 웹 프록시 8600 → 백엔드 8100으로 연결할 예정이다.
Python 웹 프록시는 빌드한 web/dist/index.html과 /assets를 제공하도록 정적 경로만 바꿨다.
health의 HTML 점검도 dist 기준이다. 게임 API/SSE 중계 로직은 유지했다.
빌드 전 루트 요청은 빌드 필요 안내와 503을 반환한다.

이번에는 빈 템플릿/자리 표시, props/emits와 폴더 계약만 만들었다.
실제 캐릭터/카드 UI, 눈 모션, HTTP/SSE 연결, 입력/리플레이 동작은 구현하지 않았다.
초기 스켈레톤을 띄워도 게임 생성/LLM 호출이 발생하지 않는다.

검증: npm production build 통과, JS 문법 검사 통과, Python 전체 unittest 79개 통과.
Python 웹 프록시에서 빌드 index와 JS/CSS assets가 200으로 제공됨도 확인했다.

### CSS 캐릭터 하나와 눈 모션

하단 인간/관전 앵커의 SlimeAvatar 자리 표시에 실제 캐릭터 하나를 붙였다.
AvatarBody.vue는 원형 몸통과 색상, AvatarEyes.vue는 기울어진 흰 알약 눈 두 개와
CSS keyframes, SlimeAvatar.vue는 두 부품의 조립을 담당한다. 기본 몸 색은 검정이고
기존 color prop으로 바꿀 수 있다. 이미지/SVG나 JS 타이머는 사용하지 않는다.

눈은 3.8초마다 3px 상하 이동하고 6.8초마다 천천히 감았다 뜬다.
motionEnabled는 GameScreen → PokerTable → PlayerSeat → SlimeAvatar → AvatarEyes로
전달한다. 관전의 readOnly와 분리하고 replay.mode가 replay일 때 모션을 끈다.
OS의 prefers-reduced-motion 설정도 적용한다. 게임/API 연결은 기존 스켈레톤 그대로다.

검증: npm production build 통과. Vite 개발 서버(5173) 실행 확인.
현재 연결된 브라우저가 없어 화면과 애니메이션의 시각 검증은 수행하지 못했다.

### 캐릭터 색상 미리보기와 눈 이동 속도

눈 상하 이동 주기를 3.8초에서 1.8초로 줄였다. 느린 깜빡임 주기는 유지한다.
App.vue의 previewView를 기존 GameScreen/PlayerSeat에 전달해 위에 코랄/파랑/보라,
하단에 검정 캐릭터를 표시한다. 실제 게임 생성 없이 같은 SlimeAvatar 컴포넌트를 재사용한다.

### 캐릭터 시선 방향

SlimeAvatar/AvatarEyes에 gazeAngle(도)과 gazeStrength(0..1)를 추가했다.
오른쪽 0, 아래 90, 왼쪽 180, 위 -90이며 strength=0이면 정면이다.
눈 쌍을 감싸는 위치용 요소에서 sin/cos로 중심을 이동하고, 내부 요소가 기존 상하/깜빡임
모션을 담당해 transform이 충돌하지 않는다. 방향 변경은 280ms로 연결하고 모션 비활성화나
reduced-motion에서는 즉시 적용한다. 몸통은 회전하지 않는다.
PlayerSeat는 seat.gazeAngle/gazeStrength를 전달한다. App 미리보기의 네 캐릭터는
테이블 안쪽을 보도록 서로 다른 각도를 설정했다. npm production build 통과.

### 테이블 표시 컴포넌트 구현

명세의 참가자 표시부터 채웠다. PlayingCard는 앞면/뒷면/없음을 구분하고 A/K/Q/J와
네 무늬를 표시한다. 뒷면은 앞면 DOM/접근성 라벨을 만들지 않으며 잘못된 앞면 값도
비공개로 처리한다. PlayerSeat는 폴드 카드를 뒷면으로 고정한다.

SpeechBubble은 최근 확정 발언을 두 줄로 제한하고 빈 상태에서도 자리를 확보한다.
전체 보기 이벤트를 PlayerSeat/PokerTable이 GameScreen으로 전달하고, GameScreen은
EventLog.focusEvent(eventId)로 해당 원문을 스크롤/포커스하고 강조한다. 같은 발언을
다시 눌러도 동작한다. 입력 초안이나 엔진의 침묵 여부는 말풍선에서 판단하지 않는다.

PlayerSeat는 칩/베팅 차례/상태/최근 행동도 표시한다. PokerTable은 A 오른쪽/B 위/
C 왼쪽/인간 아래의 배치와 AI 2명일 때 우상단/좌상단 배치를 제공한다. TableStatus는
전달받은 라운드/단계/판돈/현재 베팅/차례만 표시하며 규칙이나 금액을 재계산하지 않는다.
EventLog는 원문 스크롤/선택을 표시하고 replayAvailable과 frameId가 있을 때만
시점 이동 버튼을 노출한다. 현재 앱은 리플레이를 활성화하지 않는다.

미리보기 데이터를 src/preview/tablePreview.js로 분리하고 App.vue는 조립만 맡긴다.
예시임을 화면에 명시했다. HTTP/SSE/상태 adapter, 입력/베팅, 정확한 과거 상태 조회는
아직 스켈레톤이다. 다음은 GameSetup/GameToolbar와 인간 입력/베팅 컴포넌트다.

검증: production build와 Vue SFC 렌더링 검사(npm test) 통과. 검사는 카드 비공개,
폴드/관전, 발언 HTML 이스케이프와 긴 원문 보존, 리플레이 비활성 상태를 확인한다.
새 테스트 의존성은 추가하지 않았다. 브라우저에서의 배치/클릭 시각 검증은 미수행이다.

### 인간 좌석 간격과 말풍선 원문 스크롤

하단 인간 좌석을 64px 더 아래로 내렸다. 좌우 AI는 중앙 행을 기준으로 정렬하여
인간 행이 늘어나도 함께 아래로 밀리지 않게 했다.
말풍선은 두 줄 말줄임 대신 최대 세 줄 높이의 내부 세로 스크롤로 바꿨다.
키보드 포커스/스크롤을 지원하고 새 eventId의 발언은 처음부터 읽도록 본문을 교체한다.
기록에서 전체 보기 기능도 유지한다.

별도 질문인 폴드 후 발언은 현재 허용되지 않음을 확인했다. Table._submit과
release_deferred에서 폴드 참가자를 거절하고, observe.can_speak도 생존자만 허용한다.
이번 변경에서 게임 엔진 규칙은 수정하지 않았다.

### 폴드 후에도 대화 참여

사용자 요청으로 폴드 참가자의 발언을 허용했다. Table._submit은 라운드 참가자/모델 오류
여부를 공통 검사하고 폴드 제한은 bet 명령에만 적용한다. speak/silent와 보류 발언의
명시적 재확인은 폴드 후에도 가능하다. 발언 한도/연속 발언 제한/일반 잡담 횟수 정책은
유지하며 새 기회를 지급하지 않는다. 공개 후 발언과 폴드 후 베팅은 계속 거절한다.

observe는 발언 참가자와 베팅 생존자를 구분하고 folded_players를 추가했다. 모델에
폴드 상태와 '발언 가능, 베팅/판돈 획득 불가'를 전달한다. can_speak는 폴드만으로
꺼지지 않으며 allowed_bets/raise_options는 비어 있다. 비공개 카드 범위는 유지한다.
RoundRunner의 AI 실행 대상과 인간 입력 전처리도 이번 라운드 참가자를 기준으로 바꿔
폴드한 AI의 사건 재판단/방송/인간 잡담 응답과 폴드한 인간의 발언이 실제로 처리되게 했다.
침묵 집계에는 폴드 참가자도 포함한다. 베팅 개방의 최소 발언 기준과 완료/공개 조건은
기존 베팅 생존자를 그대로 사용하므로 폴드 참가자가 공개를 지연시키지는 않는다.
모델 오류로 failed가 된 에이전트와 다음 라운드에서 제외된 탈락자는 실행 대상에 넣지 않는다.

UI 미리보기에도 폴드한 C의 발언 예시를 추가했다. 실제 입력 UI/API 연결은 후속 작업이다.
회귀 검사는 폴드 후 발언/보류 해제/한도 소진/베팅 거절/카드 비공개/종료 후 거절,
모델 오류 참가자 제한, AI→다른 AI 방송 및 인간 잡담 응답, 인간 입력 경로를 포함한다.
검증: Python 전체 unittest 83개, 프런트엔드 렌더링 검사와 production build 모두 통과.

### 게임 설정·상단 상태와 인간 입력 컴포넌트

GameSetup은 AI 2/3명과 인간 참여/관전 선택, 생성 중 차단/오류 표시를 구현하고
create-game({aiIds,humanPlays})만 전달한다. GameToolbar는 세션/연결 상태, 라운드,
플레이/관전, 우승자와 시작/종료/현재 복귀 버튼을 담당한다. canStartNextRound와
created/ready 상태가 맞아야 시작할 수 있고 진행/정리/종료/처리 중/리플레이에서는 막는다.
GameScreen은 상단과 하단 명령 이벤트를 상위로 전달하고 replay.mode의 읽기 전용을 적용한다.

HumanControls는 초안, 남은 발언 횟수, 발언 없음과 보류 원문/명시적 재확인을 표시한다.
commands.canSpeak/canDeclareSilent/canRelease를 따르고 관전/리플레이/처리 중에는 차단한다.
Ctrl/Cmd+Enter 전송은 IME 조합 중에 실행하지 않는다. 접수 실패나 미연결 상태에서
초안을 지우지 않는다. 같은 contextKey와 원문에 대한 speechReceipt의 accepted/deferred를
받아야 비우며 게임/라운드의 contextKey가 바뀌면 초기화한다. 서버의 maxMessageChars를
받으면 입력 길이를 제한한다. 폴드는 참여 모드를 바꾸지 않아 남은 발언을 계속 쓸 수 있다.

BettingControls는 allowedBets/raiseOptions로 활성화를 결정하고 체크/콜/폴드와
최대/큰/중간/작은 레이즈 증분을 표시한다. 기본은 bet({action}), 레이즈는
bet({action:'raise',raise_size})로 전달한다. 발언 횟수 소진과 베팅 가능 여부는 별개이며
금액이나 유효한 크기를 프런트에서 다시 계산하지 않는다.

App은 조립만 맡기고 preview/useTablePreview.js와 PreviewTools.vue로 예시 상태를
분리했다. 인원/관전 선택과 내 차례/폴드/발언 소진/보류/다음 라운드 대기를 확인할 수 있다.
관전 앵커에는 칩/카드가 없고 AI 카드도 뒷면만 전달한다. 버튼 입력은 미리보기 안내에만
표시하고 실제 게임 접수 성공이나 새 사건/과거 프레임으로 생성하지 않는다.
HTTP/SSE/실제 상태 변환/게임 명령 연결과 리플레이는 여전히 후속 작업이다.

검증: npm test의 SFC 렌더링/상태 검사와 production build 통과. 새 의존성은 추가하지 않았다.
브라우저에서의 배치/클릭 시각 검증은 수행하지 않았다.

### Vue 실제 세션 연결: HTTP / SSE / 상태 조립

기본 App은 LiveGame으로 실제 서버를 연결한다. 기존 화면 예시는 GamePreview로 옮겨
`?preview=1`에서 유지한다. LiveGame은 useGameSession의 props와 컴포넌트 emits만 묶으며
GameScreen/캐릭터/카드/입력 컴포넌트는 재사용한다. 함수별 설명과 실행/검증 순서는
`web/CONNECTION_GUIDE.md`에 작성했다.

- services/gameApi.js: 기존 엔드포인트의 일반 요청과 ApiError. Authorization 헤더,
  JSON, 제한 시간만 담당하고 명령을 자동 반복하지 않는다.
- services/sseParser.js / eventStream.js: 분할 UTF-8/줄/CRLF/여러 data 줄을 처리하고
  sequence 중복 제거, Last-Event-ID 재연결, 409/ResyncRequired 재동기화, 종료를 담당한다.
  스트림 close는 구독 종료일 뿐 게임을 DELETE하지 않는다.
- services/sessionStorage.js: 한 탭의 gameId/token만 재접속 정보로 저장한다. 저장소가
  차단되면 현재 메모리로 실행한다. 토큰을 표시 데이터나 공개 사건에 넣지 않는다.
- adapters/toTableView.js: 서버 players의 생성 순서로 좌석/색을 고정하고 현재 라운드의
  확정 발언/행동을 붙인다. snapshot.visible_cards로만 카드 표시를 결정한다. 폴드 카드,
  공개 전 자기 카드/AI-only 관전은 rank/suit를 복사하지 않는다.
- adapters/toCommandState.js: 발언/베팅 권한을 분리하고 동기화/접수 재확인 중 입력을 잠근다.
- adapters/publicEvents.js: 공개 사건을 키로 합치고 화면 기록으로 변환한다.
- components/SessionNotice.vue: 서버 모드/오류/재연결과 동일 요청 재확인 버튼을 표시한다.

useGameSession의 연결 순서는 createGame → attach → refresh → perform이다.
initialize는 상태 점검/저장된 테이블 복구만 하고 새 게임/라운드를 자동 시작하지 않는다.
새 사건은 기록에 합친 뒤 GET 상태를 갱신한다. 동시에 몰린 알림의 조회는 합치고,
stream_cursor/event_id 비교와 mutationEpoch로 오래된 응답이 새 접수 결과를 덮어쓰지
못하게 한다. generation으로 종료/화면 이동 이후 도착한 응답도 무시한다.
RoundReady는 event_id가 그대로여도 stream_cursor가 바뀌므로 반영한다.

발언/베팅은 최신 round_id와 event_id를 사용한다. 응답을 잃으면 다른 입력을 잠그고
원래 request_id/본문으로 재확인한다. 자기 Speech 사건으로 접수가 확인되면 발언/보류
공개의 불확실성도 해제한다. rejudge 거절은 최신 상태를 보여주고 사용자가 다시 선택한다.
베팅 크기나 칩을 프런트에서 재계산하거나 상황 변경 후 자동으로 베팅하지 않는다.

백엔드는 기존 GameSession.snapshot에 recent_events/is_silent와 rules의
max_message_chars/event_history_limit만 보충했다. 새 엔드포인트나 프록시 변경은 없다.
최근 사건은 현재 라운드의 공개 기록이며 한도를 적용한다. 새로고침 시 현재 상태와 최근
말풍선/행동을 복구하지만 과거 모든 라운드의 정확한 상태 탐색은 별도 history 작업이다.
MCPClient/OllamaLLM과 기존 게임 실행 경로를 그대로 사용한다.

검증: Node 네트워크/세션 테스트 14개와 SFC 렌더링 검사, production build 통과.
기존 서버 계약 테스트 11개 통과. 임시 포트/저장소의 fake 서버와 실제 Python 웹 프록시를
사용한 통합 검사에서 인간 참여/AI-only 관전, 다음 라운드, 좌석 유지, 재접속, 종료를 확인했다.
실제 Ollama 모델과 브라우저 화면/클릭은 이번 검증에서 실행하지 않았다.
전체 Python 검사는 실행 당시 83개 중 82개 통과했다. 편집 중인 graph_practice.build_graph가
더 이상 NotImplementedError를 던지지 않아 기존 스텁 가정 테스트 1개가 실패했다.
이번 연결 작업에서 연습 파일과 해당 검사는 변경하지 않았다.
