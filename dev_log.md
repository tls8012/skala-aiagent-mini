# 개발 로그

## 작은 변환

### 2026-10-20 — Ollama 통신과 운영 업무 로직 분리

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

외부 네트워크와 실제 모델 없이 stub 및 HTTP mock으로 검증했다.
실제 Ollama 서버를 사용하는 종단 간 응답 품질은 검증하지 않았다.

## 큰 변환

### 검토 후보 — 아직 적용하지 않음

- OpenAI 통신도 `TextClient`에 맞추고 운영 업무 로직을 공유한다.
  구조화 출력 지원 수준과 스트리밍 차이는 어댑터에서 명시적으로 처리한다.
- 업무별 설정/정책으로 시스템 목록, 태스크 종류, 기본 대상, 결과 보정 규칙을 주입한다.
- graph의 특정 MCP 도구명·리소스명과 운영 데이터 해석을 업무 서비스로 분리한다.
  MCP 도구의 발견만으로 업무 의미나 실행 정책까지 자동으로 정해지지는 않는다.
- graph가 의존하는 업무 인터페이스를 명시하고 `SupervisorDeps`의 `Any` 범위를 줄인다.
- 동기 모델 호출을 비동기로 통일하는 변경은 graph와 다른 백엔드까지 함께 검토한다.
