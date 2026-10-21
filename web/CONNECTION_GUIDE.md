# 실제 게임 연결 코드 읽기

## 실행과 현재 범위

프로젝트 루트에서 각각 다른 터미널로 실행한다.

```sh
LLM_BACKEND=fake .venv/bin/python server.py
```

```sh
.venv/bin/python web_demo_server.py
```

```sh
npm run dev --prefix web
```

http://127.0.0.1:5173 에서 AI 인원과 참여 여부를 선택하고 테이블을 만든다.
이어 첫 라운드 시작 → 발언 → 자신의 베팅 차례에 행동 → 공개 → 다음 라운드 순서다.
상황이 바뀌어 베팅이 거절되면 최신 상태를 보고 다시 선택한다.
우측 발언/사건을 클릭하면 당시 테이블로 이동한다. 하단에서 라운드를 고르거나
처음/이전/다음/마지막·슬라이더로 탐색하고 '현재로 돌아가기'로 최신 화면을 본다.
과거를 보는 동안 실제 게임은 계속 진행하며 모든 게임 입력은 잠긴다.
기본 fake 모드는 실제 LLM/MCP를 호출하지 않는다. `LLM_BACKEND=ollama`로 서버를
실행하면 기존 Ollama/MCP 경로를 사용하며 화면의 연결 안내에 모델을 표시한다.

빌드한 화면을 쓸 때는 `npm run build --prefix web` 후 8600의 웹 프록시를 열면 된다.
`npm run preview`는 Vite 빌드 확인용이며 게임 프록시를 제공하지 않는다.
컴포넌트 예시는 개발 서버나 Python 웹 프록시의 `/?preview=1`에서 계속 볼 수 있다.
화면을 나가거나 예시로 이동해도 서버 게임은 멈추지 않는다. 테이블 종료는 별도 명령이다.

## 전체 흐름

```text
App → LiveGame → GameScreen → GameSetup / GameToolbar / HumanControls
          │                          사용자 의도를 emit
          └─ useGameSession ←───────────────┘
                 ├─ gameApi ── HTTP ── 웹 프록시 ── server.py
                 ├─ eventStream ← SSE ← 웹 프록시 ← server.py
                 ├─ toTableView / toCommandState → 표시/입력 props
                 └─ publicEvents → 기록과 말풍선
          └─ useReplay → 세션의 기록 조회 → replayApi/gameApi → /history
                 └─ 선택 snapshot → toTableView → 같은 테이블 컴포넌트
```

`App.vue`는 실제 화면과 예시 화면 선택만 맡는다. `LiveGame.vue`는 세션의 값을
GameScreen에 전달하고 버튼 이벤트를 세션 함수에 연결한다. 화면 컴포넌트 안에는
HTTP 호출이나 Bearer 토큰을 넣지 않는다.

## 1. gameApi.js — 일반 요청

`createGameApi()` 안의 `request()`가 헤더, JSON, HTTP 오류와 제한 시간을 처리한다.
`gamePath()`는 gameId를 URL 경로로 인코딩한다. accessToken은 Authorization 헤더에만
넣고 URL이나 화면 객체에는 넣지 않는다. 전송 계층은 요청을 자동 반복하지 않는다.

외부 함수는 기존 서버 엔드포인트와 일대일로 대응한다.

| 함수 | 요청 |
|---|---|
| checkHealth | GET /health |
| createGame | POST /games |
| getGame | GET /games/{id} |
| listReplayRounds | GET /games/{id}/history |
| getReplayFrames | GET /games/{id}/history?round_id=N |
| startRound | POST /games/{id}/rounds |
| submitCommand | POST /games/{id}/commands |
| releaseSpeech | POST /games/{id}/commands/release |
| closeGame | DELETE /games/{id} |

`ApiError.uncertain`은 서버가 처리했는지 확인할 수 없는 네트워크/서버 오류다.
명시적인 규칙 거절과 구분하여 같은 요청을 재확인할 때 사용한다.

## 2. eventStream.js와 sseParser.js — 사건 수신

`subscribeGameEvents()`는 fetch 스트림으로 인증 헤더를 붙여 구독한다.
`TextDecoder`가 분할 UTF-8을 복원하고 `createSseParser()`가 분할 줄, CRLF,
heartbeat 주석과 여러 data 줄을 처리한다. 사건 JSON의 type과 event 이름,
id와 sequence가 맞는지도 검사한다.

연결이 끊기면 마지막으로 처리한 sequence를 after/Last-Event-ID로 보내 재연결한다.
동일 sequence는 다시 처리하지 않는다. 401/403/404는 자동 재시도를 멈춘다.
409나 ResyncRequired를 받으면 onResync가 현재 상태를 가져온 후 새 stream_cursor로
이어 받는다. `close()`는 스트림과 재시도 타이머만 정리하며 게임을 DELETE하지 않는다.
heartbeat나 재접속 시간은 게임의 침묵/행동 판단에 사용하지 않는다.

## 3. useGameSession.js — 게임 연결 조립

가장 먼저 읽을 함수는 `createGame → attach → refresh → perform`이다.

- `initialize()`: 저장된 테이블이 있으면 현재 상태를 조회하고 재접속한다. 없으면
  서버 상태만 확인한다. 페이지를 열었다고 새 게임/라운드를 자동 실행하지 않는다.
- `createGame()`: 생성 응답의 game과 토큰을 나누고 상태 적용, 재접속 정보 저장,
  SSE 구독을 시작한다. 기존 테이블이 열려 있으면 다시 생성하지 않는다.
- `attach()`: SSE 사건을 기록에 합치고 현재 상태 조회를 요청한다. 사건이 한꺼번에
  오면 조회를 합친다. GameClosed는 연결/저장 정보를 정리한다.
- `refresh()`: 한 번에 한 조회만 실행한다. 조회 중 새 사건이 오면 한 번 더 조회한다.
  조회가 명령 접수 전 시작됐으면 명령 완료 후 그 결과를 버리고 다시 읽는다.
- `applySnapshot()`: gameId와 stream_cursor/event_id를 확인해 오래된 응답을 버린다.
  서버의 recent_events를 공개 사건 목록에 합친다.
- `perform()`: 처리 중 중복 입력을 잠그고 응답의 상태·접수 결과를 적용한다.
  speak/silent/bet는 최신 round_id/event_id로 PlayerCommand를 만든다.
- `retryLastCommand()`: 응답이 불확실할 때 저장했던 동일 본문과 request_id로 확인한다.
  서버가 요청 ID를 기억하므로 동일 베팅을 두 번 적용하지 않는다. 시작/보류 해제/종료도
  원래의 요청으로 확인한다. 발언은 자기 Speech 사건으로 확인되는 경우에도 복구한다.
- `dispose()`: 화면이 사라질 때 구독을 끊고 이후 도착하는 응답을 무시한다. 게임은 유지한다.

`generation`은 종료/화면 이동 이전의 비동기 결과가 새 연결을 건드리지 못하게 하고,
`mutationEpoch`는 명령 완료 전 시작된 GET이 접수 결과를 덮어쓰지 못하게 한다.
`pending`은 요청 중, `uncertain`은 접수 여부 재확인이 필요한 상태다.

SSE의 sequence와 게임의 event_id는 별개다. 예를 들어 RoundReady는 카드를 다시
공개하는 게임 사건 없이 발생하므로 event_id가 같아도 stream_cursor가 증가하면
새 상태로 적용해야 한다. 명령의 basis_event에는 event_id만 넣는다.

서버가 `rejudge=true`로 거절한 베팅은 자동으로 재선택하지 않는다. 최신 상태로
갱신하고 사용자가 새 요청으로 다시 선택하게 한다. 접수 불명확 상태에서는 다른
입력을 잠그고 '같은 요청 다시 확인' 버튼을 표시한다.

## 4. adapters — 표시와 권한

`toTableView()`는 players의 생성 순서로 좌석/색을 유지한다. 베팅 시작 순서가
회전해도 캐릭터를 재배치하지 않는다. 현재 라운드와 현재 event_id까지의 확정 발언/행동을
찾아 말풍선과 최근 행동으로 바꾼다. 게임 결과나 칩은 프런트에서 계산하지 않는다.

카드는 현재 snapshot.visible_cards만 읽는다. 공개 전 자신의 카드, 폴드 카드,
AI-only 관전의 카드는 뒷면이며 rank/suit를 복사하지 않는다. 관전자 앵커에는 카드와 칩을
만들지 않는다. 나중의 RoundEnded 사건에 카드가 있어도 현재/과거 상태에 역으로 채우지 않는다.

`toCommandState()`는 서버 관찰의 발언 권한과 베팅 선택지를 별도로 읽는다.
폴드한 인간도 남은 발언이 있으면 말할 수 있고, 발언이 소진돼도 베팅은 할 수 있다.
SSE와 현재 상태가 아직 맞지 않거나 재확인이 필요한 동안에는 명령을 잠근다.

`publicEvents.js`는 event_id/sequence에 맞는 키로 중복을 제거하고 화면용 기록을 만든다.
`sessionStorage.js`는 한 탭의 gameId/token만 저장한다. 저장소가 차단되면 현재 탭의
메모리로 계속 실행한다. 브라우저를 새로 열었거나 서버가 재시작돼 게임이 없어졌다면
새 테이블을 만들어야 한다.

## 5. useReplay.js — 실시간과 과거 화면 분리

`useGameSession`은 계속 최신 상태와 SSE/입력 초안/접수 결과를 관리한다.
토큰을 클로저에 둔 `readHistory()`가 replayApi의 조회를 호출하므로 useReplay나 화면에는
토큰을 전달하지 않는다. 기록을 선택해도 liveState를 과거 상태로 덮어쓰지 않는다.

- `refreshRounds()`: 현재 게임의 라운드 목록을 읽는다. 새 SSE 상태마다 목록을 갱신하되
  진행 중 조회는 합친다. 보는 라운드에 새 프레임이 있으면 캐시에 추가하고 선택은 유지한다.
- `selectRound()/seekFrame()`: 라운드를 조회한 뒤 정확한 frame_id를 선택한다. 선택 번호로
  늦은 응답의 화면 덮어쓰기를 막고 game_id 세대로 다른 게임의 응답을 버린다.
- `seekPosition()/step()`: 선택 라운드의 프레임 배열 안에서 이동한다. 사건 번호는 인덱스가 아니다.
- `returnLive()`: 선택 화면을 해제하고 이미 갱신된 최신 화면으로 복귀한다.
- `runLive()`: LiveGame에서 생성/시작/종료/발언/침묵/보류 공개/베팅/요청 재확인의 모든
  전달 경로를 검사한다. replay 중에는 함수를 실행하지 않는다. 조회와 연결 확인만 허용한다.

`LiveGame`이 displayView/displayEntries/과거의 남은 횟수를 선택한다. 입력의 contextKey와
접수 결과는 실시간 값을 유지하고 베팅 선택지는 과거 화면에서 비운다. 따라서 과거 탐색이
입력 초안을 이전 라운드로 바꾸지 않는다. 과거 화면에서는 눈 애니메이션도 멈춘다.
실패하면 이전 표시를 유지하고 재조회/현재 복귀를 제공한다. 삭제/404/게임 변경 시 캐시를 지운다.

## 백엔드에서 추가한 것

`agents/replay_history.py`의 `ReplayHistory`는 프레임/라운드/사건 링크를 메모리에 보관한다.
`Table._emit()`이 상태 변경 직후 capture를 호출한다. Speech는 Conversation.add와 응답 상태를
반영한 다음 별도로 저장한다. 마지막 BetPlaced와 정산 RoundEnded도 별개의 정확한 상태다.
생성/조회 모두 깊은 복사를 사용하며 당시 허용된 카드와 최신 발언/행동만 스냅샷에 담는다.

`GameSession`은 기존 인증을 거쳐 history를 조회하고 SSE/recent_events의 복사본에 frame_id만
붙인다. 원본 Table.events/JSON/RAG/MCP 계약은 유지한다. `server.py`에 history GET 하나를
추가했으며 목록/라운드는 쿼리로 구분한다. 웹 프록시는 기존 일반 중계를 그대로 사용한다.

SSE/recent_events FIFO가 밀려나도 현재 게임의 모든 라운드는 조회할 수 있다. 게임 finished는
기록을 보존하고 DELETE/서버 재시작은 폐기한다. 새로고침하면 현재 화면으로 접속한 뒤
라운드 선택으로 과거를 다시 조회한다. 이전 game_id 보관함/영구 저장/자동 재생은 제공하지 않는다.

## 검증

```sh
npm test --prefix web
npm run build --prefix web
.venv/bin/python tests/run_web_integration.py
```

Node 검사는 인증/SSE/재연결/요청 재확인 외에 정확한 프레임 이동, 빠른 연속 선택,
조회 실패/재시도, 실시간 갱신 중 과거 고정, 캐시 정리와 명령 차단을 확인한다.
Python 검사는 사건 경계/깊은 복사/카드 비공개/FIFO와 별도 보관/history 인증을 확인한다.
마지막 통합 검사는 임시 저장소와 임시 포트에 fake 서버와 실제 Python 웹 프록시를 띄워
인간/AI-only 라운드, 실제 진행 중 과거 고정, 이전 라운드 새로고침 복구, 종료 정리를 확인한다.
실제 모델이나 MCP는 호출하지 않는다. 브라우저 배치/클릭의 시각 검증과는 별개다.
