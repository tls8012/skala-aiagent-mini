# 엔진 기반 실험 구현 명세

세 단계의 실행기와 시간/입력/출력 로그를 구현했다. 아래 단계별 명세를 실행 코드와 함께 사용한다.
실험 결과는 루트 [exp_log.md](../exp_log.md)에 실행 ID와 함께 요약한다.

## 실행과 로그 확인

프로젝트 루트에서 실행한다. 웹 서버나 게임 서버를 띄울 필요가 없고, Ollama 모드에서는
로컬 Ollama와 config에 지정된 판단/임베딩 모델이 필요하다. OpenAI 판단도 지원하며
`--backend openai`와 OPENAI_API_KEY를 사용한다. 검색 임베딩은 기존 Ollama를 유지한다.
새 라이브러리는 추가하지 않았다.

```sh
# 먼저 모델 호출 없이 엔진/그래프/로그 배선 확인
.venv/bin/python -m experiments.run --stage 1 --cases 1 --backend fake

# 실제 모델 실험: 같은 30개 상황을 각 조건에 대응시킨다
.venv/bin/python -m experiments.run --stage 1 --cases 30
.venv/bin/python -m experiments.run --stage 2 --cases 30
.venv/bin/python -m experiments.run --stage 3 --cases 30

# 한 조건만 소량 확인하거나 모델을 바꿀 때
.venv/bin/python -m experiments.run --stage 2 --cases 1 --conditions search_only --model qwen3:8b

# GPT-6 Luna로 같은 경로 실행
.venv/bin/python -m experiments.run --stage 1 --cases 1 --backend openai
```

기본 backend는 GameConfig.llm_backend(현재 ollama)다. `--backend fake`에서는 실제 그래프/게임 엔진/파일 메모는 사용하지만
모델과 검색 결과는 smoke용 응답이다. manifest의 evidence_run=false로 구분하며 성능 근거로 쓰지 않는다.
cases는 1..30, 기본 30이다. 1단계 3조건, 2단계 4조건, 3단계 상대별 2조건이 기본이다.
`--conditions`에 해당 단계의 이름을 여러 개 넘기면 일부 조건만 실행한다.
`--seed`, `--output`, `--timeout`(판단/라운드), `--index-timeout`(도구 준비)을 바꿀 수 있다.
일부 시행이 실패해도 나머지를 실행하고 종료 코드는 1로 반환한다. 원본 실패 로그는 남는다.
CLI는 외부 LangSmith 전송을 끄고 로컬 파일에 기록한다.

결과는 `experiments/results/<run_id>/`에 생성되고 버전 관리에서 제외한다.

| 파일 | 확인할 내용 |
|---|---|
| summary.md / summary.json | 조건별 결과, 대응 비교, 노드·모델·도구 평균/중앙값/p95/합계 시간 |
| responses.csv | 개별 모델 요청의 응답 시간, call_id/decision_id/trial_id, 성공/오류, 토큰 수 |
| timings.csv / timings.jsonl | 전체 구간 타임라인과 각 노드 실행/검증 재시도/취소/오류 시간 |
| model_calls.jsonl | 최종 system/user/schema와 실제 모델 원문·usage. call_id로 시간과 연결 |
| decisions.jsonl / events.jsonl | 입력·제안·실제 행동 접수·확정 사건. decision_id로 연결 |
| tools.jsonl / research.jsonl / contexts.jsonl | 메모 입력/저장 read-back, 마스킹/쓰기 억제, 실제 검색 결과 |
| trials.jsonl / rounds.jsonl / errors.jsonl | 사례별 상태/유효 여부, 정산·칩 변화, 실패. 데이터가 없으면 해당 파일은 생성하지 않음 |
| review.csv | 발언/메모 근거를 사람이 평가할 자리. 보고서 재생성 시 기존 평가를 덮어쓰지 않음 |
| manifest.json / data/ | 실제 설정/모델 digest/코드 해시/전체 시간 및 격리된 공개 경험·메모·아카이브 |

보고서를 다시 만들려면 `.venv/bin/python -m experiments.report experiments/results/<run_id>`를 쓴다.
자동 집계는 행동과 시간에 한정한다. 모델의 근거가 사실에 맞는지는 review.csv에서 평가한다.

### 시간의 의미

- perf_counter 기반 초 단위 경과 시간을 기록하고 UTC 기록 시각/실행 시작 기준 offset도 남긴다.
- manifest.total_duration_s는 기록기 시작부터 준비/시행/MCP 정리까지다. 그 뒤의 보고서 파일 생성은 제외한다.
- trial_total은 개별 조건의 상태 준비·도구 연결·판단/라운드·정리 시간이다. 공용 과거 뱅크 생성은 별도 preparation이다.
- LangGraph callback에서 route/research/answer/validate/remember의 실제 각 실행을 측정한다.
  검증 재시도도 별도 span이며, summary 경로도 기존 answer/remember 이름으로 기록한다.
- model/llm_complete는 선택한 공급자의 동기 HTTP complete() 왕복 시간이다. 예전 로그는 ollama_complete라는 이름이다.
  서버 큐·모델 로딩·생성·전송을 포함한다.
  스트리밍이 아니므로 첫 토큰 시간(TTFT), 순수 GPU 생성 시간이나 prefill/decode 분리는 제공하지 않는다.
- tool 시간은 MCP 왕복(클라이언트 락 대기 포함)이다. 검색 내부 임베딩/Chroma 각각의 시간은 기존 도구가
  반환하지 않으므로 분해하지 않는다. 색인 준비·probe는 preparation, 실제 검색은 research/tool로 구분한다.
- 하위 시간을 포함하는 inclusive span이다. answer 안에 모델/메모 읽기 시간이 포함되므로 합산하면 중복이다.
  병렬 실행/취소도 있어 노드 합계를 실제 전체 시간으로 취급하지 않는다. JSON의 status_counts로 상태도 본다.
- 모델이 차가운지 따뜻한지는 강제로 맞추지 않는다. 최초 모델 로딩이 포함될 수 있고 조건 순서는 시드로 섞어 기록한다.
  메모 read-back 등 관찰 오버헤드도 포함되며 별도 audit span을 남긴다.
- 그래프 취소 이후에도 동기 HTTP 요청은 스레드에서 끝날 수 있다. 응답과 시간 로그가 끝날 때까지
  model_inflight_drain으로 기다린 뒤 다음 조건으로 넘어간다. 원래 HTTP 제한 시간은 기존 클라이언트를 따른다.

## 1. 읽는 순서와 범위

1. [STAGE1_INFORMATION.md](STAGE1_INFORMATION.md): 정보 주입에 따른 결정·발언·메모 변화.
2. [STAGE2_ABLATION.md](STAGE2_ABLATION.md): 검색/메모의 네 조건 비교.
3. [STAGE3_HEADS_UP.md](STAGE3_HEADS_UP.md): 공격적/수비적 상대와 실제 1대1 라운드.

모든 실험은 로컬 Python에서 실행한다. server.py/web_demo_server.py/HTML/SSE를 거치지 않는다.
Ollama 판단 호출은 실제로 수행한다. fake는 배선 점검에만 사용하고 모델 효과 결과에 섞지 않는다.
게임 규칙·그래프·프롬프트·통신·메모/검색 저장을 새로 만들지 않는다.

## 2. 재사용 경계

```text
실험 설정/시나리오
  → Table.start_round(cards=...)
  → Table.submit(준비 발언/베팅)
  → Table.observe(A) + 실제 공개 event
  → build_graph(실험용 PokerLLM 경계 객체).ainvoke(...)
       → PokerLLM.route/research/decide/remember
       → PlayerLLM → llm_controller → OllamaLLM / OpenAILLM
       → Table.call_player_tool → 조건 제어 어댑터 → 기존 MCPClient
  → Table.apply_decision(A, decision, request_id)
  → 원본 결정/메모/접수 결과/상태/사용량 기록

3단계: 위 그래프와 고정 정책 상대를 RoundRunner에 주입 → run_round()
```

| 기존 코드 | 사용 방식 |
|---|---|
| agents/config.py GameConfig | 모델/칩/상한/시드/저장 경로. 공통 기본값과 변경값 모두 manifest에 기록 |
| agents/table.py Table | 참가자, 배분, 명령 검증, 관찰, 실제 행동 접수, 아카이브 |
| agents/cards.py, betting.py, round.py | 덱·서열·칩·베팅·폴드·정산 그대로 사용 |
| agents/graph.py build_graph | 동일한 실행용 그래프와 출력 검증 사용. 연습 그래프는 사용하지 않음 |
| agents/poker_llm.py PokerLLM | 기존 업무 프롬프트/스키마/기억 갱신. 필요한 research 경계만 실험용 하위 클래스에서 제어 |
| agents/player_llm.py PlayerLLM | 세 개인 메모 자동 첨부 경로 유지 |
| agent/ollama_client.py OllamaLLM | 실제 HTTP/JSON 스키마/usage 재사용. 실험용 관찰 래퍼로 최종 입력/응답 기록 |
| agent/llm_controller.py / openai_client.py | 공급자 선택과 OpenAI Responses API. 모델·출력 한도는 GameConfig 사용 |
| agent/mcp_client.py MCPClient, agents/mcp_server.py | 메모 저장, Chroma/임베딩, 경험 적재/색인 상태 확인 |
| agents/runner.py RoundRunner | 3단계 사건 전달·병렬 AI 판단·상태 적용·회고 |
| agents/demo.py ScriptedPokerLLM | 참고용. 기본 체크/콜을 공격적/수비적 상대 정책으로 간주하지 않음 |

Table(['A', 'B']) 또는 Table(['A', 'C'])는 현재의 AI 최소 2명 조건을 충족한다.
heads-up을 위해 엔진 인원 제한을 바꾸거나 가짜 세 번째 참가자를 만들 필요가 없다.

## 3. 파일과 역할

단계별 진입점과 공통 실행/관찰 코드를 나눈다. 모델 판단/규칙은 기존 코드를 재사용한다.

| 파일 | 클래스/함수 책임과 연결점 |
|---|---|
| run.py | CLI, 모델 확인, 단계 선택과 보고서 생성 |
| common.py | ExperimentConfig, RunRecorder, NodeTimingHandler, ObservedTable. JSONL/시간/행동 접수 관찰 |
| fixtures.py | Scenario/scenarios(), build_table(), history_bank(), memoir(). 실제 엔진으로 고정 상황/과거 자료 생성 |
| backends.py | ExperimentPokerLLM, ToolGate, RecordingClient, ObservedGraph. 조건 제어와 관찰 |
| execution.py | run_cases()/run_trial(). 조건별 격리/검색 준비/판단 또는 라운드 실행과 실패 수집 |
| stage1.py | 30개 고정 판단 상황 × 3정보 조건. 한 결정과 실제 적용 결과 수집 |
| stage2.py | 30개 고정 판단 상황 × 네 조건. 실제 검색 준비/메모 입력과 출력 추적 |
| stage3.py | RoundRunner로 A–B/A–C 독립 라운드 실행. 조건 대응과 칩 정산 확인 |
| opponents.py | FixedPolicy의 aggressive/defensive/reference 정책. 메모/검색/LLM 없음 |
| report.py | 행동 변화·근거 평가·칩 대응 차이·오류/지연/호출 수 요약. 원본 로그는 변경하지 않음 |
| results/<run_id>/ | manifest/원본 JSONL/CSV/요약/격리된 data. 사례는 코드+시드로 생성하고 trials.scenario에 저장 |

내부 실험 설정/집계에는 dataclass와 dict를 사용해도 된다. 기존 GraphInput/AgentDecision/
PlayerCommand 등 외부 경계 계약은 그대로 검증한다. 프로덕션 코드 변경은 실제 연결에
필요한 최소 관찰 훅이 확인될 때만 검토한다. 현재 구현은 실험용 하위 클래스/콜백을 사용하며
기존 agents/agent의 실행 코드는 수정하지 않았다.

## 4. 정보 조건 제어

ToolGate는 Table이 부여한 game_id/agent_id/round_id를 유지하고 실제 MCPClient.call_tool에 위임한다.
기존 mcp 연결의 start/stop 수명도 감싼 실행기가 책임진다. 출처를 모델이 정하게 하지 않는다.

- 검색 off: ExperimentPokerLLM.research가 skipped/빈 matches를 반환한다. Chroma 검색을 호출하지 않는다.
- 검색 on: 1단계만 사전 지정 응답을 반환한다. 2/3단계는 기존 research와 실제 MCP 검색을 사용한다.
- 메모 입력 off: read_memories는 round/game/long 모두 빈 문자열, read_memo도 해당 scope를 비운다.
- 메모 입력 on: 기존 도구에 위임한다. 실제 첨부 전후 내용을 기록하고 길이 상한을 그대로 적용한다.
- 메모 off에서 생성된 write_memo는 실험 감사 로그에 기록하되 활성 메모 파일에는 저장하지 않는다.
  이는 조건별 입력 차단을 위한 명시적 실험 모드다. 실제 저장 여부를 로그에 suppressed로 표시한다.
- 1단계의 메모 입력은 모두 빈 상태로 시작하되 출력 쓰기/읽어 확인은 허용한다. 한 결정 후 다음
  비교 실행으로 넘어가기 전에 저장소를 새로 만든다. 입력 기억 효과와 근거 출력 검사를 구분한다.

기존 PlayerLLM/PokerLLM의 프롬프트 본문을 조건마다 다시 작성하지 않는다. 빈 메모 표제 등
공통 형식은 유지하고 정보 내용만 바꾼다. 추가 출력 유도 문장이 필요하면 전 조건에 동일하게 적용한다.
“관련 기록이면 무조건 raise” 같은 정답 힌트는 넣지 않는다.

## 5. 상태 구성과 정보 누출 방지

- Card/Deck으로 중복 없는 배분을 생성하고 Table.start_round(cards=...)에 전달한다.
- 필요한 과거 공개 발언/베팅은 Table.submit으로 적용한다. 사건과 Observation을 수작업으로
  서로 모순되게 만들거나 칩/차례/발언 횟수를 직접 바꾸지 않는다.
- 최소 발언을 충족하고 A의 실제 베팅 차례를 만든다. 1/2단계 주 평가에서는 force_bet=true,
  can_speak=true를 확보한다. A가 직전 발언자이거나 발언을 소진한 사례를 잘못 실패로 세지 않는다.
- 동일 사례의 조건 사이에는 game_id 등 추적용 값만 다르고 관찰 카드/칩/대화/허용 행동은 같아야 한다.
  비교용 관찰 해시를 만들 때 game_id만 정규화하고 의사결정 필드는 제거하지 않는다.
- 입력 event는 해당 Table의 공개 사건에서 선택한다. BetTurn을 사용할 경우 기존 runner와 같은
  타입/현재 event_id 규약을 쓴다. 근거 없는 interpretation을 임의로 만들지 않는다.
- 숨은 배분 정답은 평가 전용 파일에 분리한다. 모델에는 Table.observe(A)에서 허용된 정보만 준다.
- 과거 기록은 완료된 엔진 라운드의 공개 아카이브를 쓴다. 폴드로 공개되지 않은 카드를 근거로
  “B가 거짓말했다”고 표시하지 않는다. 검증 불가능은 거짓/참과 별도 라벨이다.

## 6. 저장소·검색 준비

실험 실행별, 조건별, 사례별로 별도 data_dir를 사용한다. 특히 장기 메모는
data/memos/<agent_id>/opponents.txt에 있으므로 game_id 분리만으로 오염을 막을 수 없다.
기존 agents/data나 사용자가 플레이한 데이터를 실험 저장소로 사용하지 않는다.

기존 MCPClient는 subprocess 환경을 상속한다. export_mcp_config를 사용해 각 MCP 프로세스가
자기 data_dir를 보게 한다. 여러 조건을 동시에 띄우며 전역 os.environ을 바꾸지 않는다.
기본 실행은 조건을 순차 시작/종료하고, 조건 실행 순서는 사전 시드로 섞어 기록한다.

GameTools._load는 경험 파일이 없으면 기본 20판을 자동 생성한다. 격리 저장소에 의도한 경험
뱅크를 미리 준비하고 내용 해시를 검증해 기본 seed 자료가 몰래 섞이지 않게 한다.
기존 store_experience/공개 기록 검증/색인 경로를 재사용한다. 필요하면 store_experience의
20개 FIFO를 채우는 준비 라운드를 먼저 적재하고, 실제 유지된 ID/본문을 검사한다.
일반 데모의 기본 20판을 특정 B/C의 성향 근거로 간주하지 않는다.

현재 구현은 history_bank가 별도 엔진 라운드 20개를 완료하고 _public_record로 검증한
공개 export를 경험 파일에 먼저 넣는다. 이후 실제 MCP 검색으로 기존 색인 경로를 시작한다.
서로 독립된 과거 라운드의 번호는 뱅크 내 순번으로 정규화하며, 원래 game_id/round_id와
본문 해시는 fixture-provenance.json에 보존한다. 현재 평가 라운드는 경험 뱅크에 자동 추가하지 않는다.

2/3단계는 인덱싱 종료와 오류 없음, 사전 probe 검색의 ready 및 예상 자료 존재를 확인하고 시작한다.

게임 판단과 준비 probe는 상대 opponent_id를 명시해 Chroma의 player_id로 제한한다.
실제 검색 match에는 round_context={experience_id, round_id, result}가 붙는다. result는
메타데이터로 연결한 같은 과거 라운드의 공개 결과이며 폴드 카드는 포함하지 않는다.
이 필드가 없었던 이전 실행과 수정 후 실행은 별도 run_id/코드 해시로 비교한다.
준비 단계 호출/시간과 측정 단계 호출/시간을 분리한다. ready여도 실제 질의가 엉뚱한 사건을
찾을 수 있으므로 본 질의/결과 ID/메타데이터/거리/본문도 남긴다. 근거가 발언 청크에만 있고
공개 결과가 검색되지 않았다면 검증된 과거 사실을 읽은 것으로 평가하지 않는다.
메모는 같은 경험 뱅크에서 만든 고정 요약으로 준비하고 원본 출처 대응표를 남긴다.

## 7. 로그와 판정 계약

기본 키: run_id, stage, case_id, condition, opponent_policy, seed, order, game_id, round_id,
decision_id, basis_event. 개인 정보 대신 고정 A/B/C ID를 사용한다.

| 기록 | 필수 내용 |
|---|---|
| manifest.json | 실제 GameConfig, 모델 이름과 가능한 모델 digest, 프롬프트/코드/자료 해시, temperature, 시드, 계획 건수, 조건 순서 |
| decisions.jsonl | 입력 observation/event/force_bet, 실제 읽은 메모, 최종 system/user/schema, 검색 응답, 모델 원문, 검증된 decision, 재시도/피드백, 시간/usage |
| 행동 접수 기록 | Table.apply_decision의 각 CommandResult와 이후 공개 사건, 정규화한 행동(action/raise_size/실제 증가량), 거절·보류·재판단 여부 |
| 메모 기록 | 호출 전 내용, memory_updates 제안, 쓰기 성공/억제, 실제 파일 read-back, summary 호출에 의한 변경과 구분 |
| rounds.jsonl | 초기/종료 칩, delta, 승자/폴드/공개 결과, 실제 라운드 완료 여부, 상대 행동, 오류/자동 대체 행동 |
| review.csv | 사례/조건/결정 ID, 참조한 출처, 발언/메모 근거 점수, 사실성, 반례, 사람의 평가 사유 |

그래프 remember는 Table.apply_decision보다 먼저 실행된다. 따라서 메모가 저장됐다는 사실은
베팅이 실행됐다는 증거가 아니다. 잠정 계획/검증된 결정/엔진 접수/실제 사건을 따로 남긴다.
1/2단계는 메모 저장 직후 read-back을 확보하고, round settlement/회고가 이를 덮어쓴 후 읽지 않는다.
3단계의 다음 라운드/회고 메모 역시 출처 결정과 시간 순서를 구분한다.

현재 Table 아카이브에는 개인 메모·모델 제안·usage가 없다. on_event만 연결해서 전부 수집했다고
가정하지 않는다. 실험용 graph/backend/LLM 관찰 래퍼에서 기록하고 기존 출력/오류를 그대로 반환한다.
원본 Table.events/RAG 아카이브에 실험용 필드를 끼워 넣지 않는다.

근거 평가는 숨은 추론을 요구하지 않고 기존 공개 text와 memory_updates에서 다음을 따로 매긴다.

| 점수 | 관찰 기준 |
|---|---|
| 0 | 관련 근거가 없거나 입력에 없는 과거 사실을 지어냄 |
| 1 | 상대/과거 사실을 언급하지만 현재 판단과의 연결은 불분명 |
| 2 | 실제 제공된 과거 사실 → 현재 주장에 대한 해석 → 행동/잠정 계획을 명시적으로 연결 |

추가로 supported/unsupported/unverifiable, stale_memory, overgeneralization을 별도 표기한다.
“이전에 블러핑했으니 이번에도 반드시 거짓”은 출처 언급이 있어도 과도한 일반화로 기록한다.
정확한 문구 일치나 모델의 자기 설명만으로 인과적 활용을 판정하지 않는다.
행동 변화·근거 표기·칩 성과는 각각 별도 결과다. keyword 자동 검사는 보조 수단이며
기본 30개 규모는 사람이 원문을 확인한다. LLM judge를 필수 의존성으로 추가하지 않는다.

## 8. 실패와 집계

기존 그래프의 검증 재시도, runner의 실패/자동 체크·폴드는 유지하고 시도 수를 모두 센다.
시간 초과/모델 오류/검색 unavailable/입력 구성 오류를 구분한다. 첫 정상 결과를 얻을 때까지
같은 사례를 조용히 반복하거나 대체 사례로 채우지 않는다.

요약에는 계획 수/시도 수/유효 수/실패 원인을 함께 적는다. 대응 비교는 양쪽 조건이 유효한
사례 수를 분모로 쓰고, 빠진 쌍을 명시한다. 3단계는 자동 대체 행동을 포함한 운영 성과와
정상 완료 쌍의 보조 결과를 구분하여 실패가 잦은 조건이 유리해 보이지 않게 한다.

초기 보고는 건수/비율과 사례별 대응 차이를 우선 제시한다. 불확실성 구간을 추가하면
서로 상관된 한 라운드의 여러 결정을 독립 표본으로 세지 않고 case_id/라운드 쌍 단위로 계산한다.
결과를 보고 고른 행동/부분집합만 성과로 보고하지 않는다.

## 9. 구현 완료 조건

- 웹/서버 없이 기존 엔진·그래프·Ollama·MCP 경로를 실행하고 fake 배선 점검도 구분한다.
- 동일 사례의 입력 상태/법적 행동이 조건 사이에 같고, 정보 조건만 다름을 검사한다.
- 근거 출력과 실제 메모 저장, 제안 행동과 실제 접수 행동을 연결해 추적할 수 있다.
- off 조건의 자료 접근 차단과 실행 간 저장소 격리를 확인한다.
- 2/3단계에서 실제 검색을 수행하고 검색 준비/검색 품질 실패를 결과에서 구분한다.
- A–B/A–C에서 상대는 관찰 정보만 읽으며 검색/메모/LLM 호출이 0회다.
- 기본 표본과 총 실행량, 오류·재시도·누락을 manifest/보고서에서 재구성할 수 있다.
