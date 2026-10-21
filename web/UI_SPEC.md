# 데스크톱 포커 화면 명세

## 1. 범위와 현재 구현 수준

Vue SFC 폴더/컴포넌트/데이터 계약을 바탕으로 참가자와 테이블 표시를 구현했다.
캐릭터, 앞면/뒷면/없는 카드, 말풍선, 칩/차례/상태, 고정 좌석, 중앙 현황과 스크롤 기록이 동작한다.
App은 기본으로 LiveGame의 실제 서버 세션을 표시한다. `?preview=1`에서는 GamePreview의 예시 화면을 표시한다.
게임 설정/상단 상태·명령 버튼과 인간 발언/침묵/보류 확인/베팅 입력 컴포넌트도 구현했다.
실제 게임 상태 변환, HTTP/SSE와 인간 명령 연결, 재접속/중복 제거/접수 재확인을 구현했다.
현재 게임의 사건 직후 상태 저장, 인증된 기록 조회와 과거 화면 탐색까지 구현했다.
미리보기 전용 상태 조립은 preview/useTablePreview.js에 분리하고 PreviewTools에서 입력 상태를 바꿔 본다.
replayApi/useReplay는 서버가 저장한 실제 프레임만 사용하며 조회 실패 시 재시도/현재 복귀를 제공한다.

기준 해상도는 1920×1080, 최소 작업 폭은 1440px와 높이 900px로 잡는다.
모바일 레이아웃은 이번 설계 범위에서 제외한다. 색감/테마는 아직 확정하지 않았다.

## 2. 좌석과 화면 영역

- 인간 자리는 플레이/관전과 무관하게 항상 테이블 가장 아래에 유지한다.
- 관전 모드는 `human_id=null`로 게임을 만들고 UI의 human-anchor만 표시한다.
  이 앵커는 엔진 참가자나 베팅 순서에 추가하지 않으며 칩/카드는 없다.
- 플레이 중 탈락한 인간도 하단 자리에 남아 탈락·관전 상태를 표시한다.
- AI 위치와 색은 게임 생성 시 참가자 ID에 연결하여 유지한다.
  다음 라운드의 시작 순서 회전으로 캐릭터들이 자리를 바꾸지 않는다.
- 초기 배치 프리셋은 AI 2명/3명과 하단 인간 앵커다. 기본 표시 캐릭터는 3~4개다.
  서버가 허용하는 더 많은 참가자를 화면에서 지원하려면 별도 배치 프리셋이 필요하다.
- 기존 A→B→C→인간의 반시계 베팅 흐름에 맞춰 AI A를 우측, B를 위, C를 좌측에 둔다.
  AI 2명이면 A는 우상단, B는 좌상단이다. 시작 순서와 차례는 별도로 표시한다.

```text
                        B
          C                           A
                   판돈 / 단계

                   인간 기준점                 우측: 사건/대화 기록

                 인간 입력 / 베팅
                 라운드·프레임 탐색
```

GameToolbar는 상단, PokerTable은 넓은 중앙, EventLog는 우측에 둔다.
HumanControls와 ReplayControls는 테이블 아래에 둔다. 모바일용 재배치는 하지 않는다.
말풍선 높이와 카드 자리를 좌석마다 미리 확보하여 테이블 배치가 발언 길이에 따라 흔들리지 않게 한다.

## 3. 참가자 표현

### SlimeAvatar

사용자 참고 이미지처럼 둥근 몸과 흰 눈 두 개를 사용한다. 플레이어별 몸 색만 다르게 한다.
AvatarBody는 원형 몸통과 색상, AvatarEyes는 알약 눈 두 개와 CSS 애니메이션,
SlimeAvatar는 이 둘의 조립과 캐릭터 크기를 맡는다. SVG/이미지/JS 타이머는 사용하지 않는다.
눈은 1.8초 주기로 3px 상하 이동하고 6.8초 주기로 천천히 감았다 뜬다.
몸통 흔들기, 입, 감정 표정, LLM 표정 출력은 후속이다.
motionEnabled=false와 reduced-motion에서는 멈춘다. 리플레이 모드에서는 모션을 끈다.
입력 금지(readOnly)와 모션은 별개이므로 관전 중에도 눈은 움직인다.
여러 캐릭터를 붙일 때 좌석별 모션 위상 분산은 후속으로 처리한다.
시선은 gazeAngle(도)과 gazeStrength(0..1)로 받는다. 오른쪽 0°, 아래 90°, 왼쪽 180°,
위 -90°이며 strength=0은 정면이다. 기본값은 -45°/1이다.
AvatarEyes에서 sin/cos로 눈 두 개의 중심을 함께 옮기며 몸통과 눈 자체는 회전하지 않는다.
몸 밖으로 나가지 않게 이동 반경을 제한하고, 방향 변경은 280ms에 걸쳐 부드럽게 이동한다.
motionEnabled=false 또는 reduced-motion에서는 위치 변경도 즉시 적용한다.
현재 미리보기는 오른쪽/위/왼쪽 AI와 하단 인간 캐릭터가 테이블 안쪽을 바라보게 설정했다.
폴드/탈락/실패는 테두리·명도·텍스트 같은 게임 상태 표현으로 구분한다.
현재 백엔드는 AI별 추론 시작 신호를 주지 않으므로 대기 상태를 근거 없이 '생각 중'으로 표시하지 않는다.

### PlayingCard

카드는 캐릭터 좌상단에 걸치고 말풍선은 위쪽에 배치한다. 카드 숫자와 무늬를 크게 표시한다.
rank 14는 A, 13/12/11은 K/Q/J이며 무늬 S/D/H/C를 구분한다.
서열 계산은 UI에서 하지 않는다.

| 상태 | 카드 표현 |
|---|---|
| 참가 중인 인간, 공개 전 | 뒷면 |
| 관전자 인간 앵커 | 카드 없음 |
| 인간이 참가 중일 때 관찰 가능한 AI 카드 | 해당 시점에 허용된 앞면 |
| AI-only 관전, 공개 전 | 앞면 정보 없음(비공개 뒷면) |
| 폴드 | 비공개 뒷면, 결과에서도 새로 공개하지 않음 |
| 공개 | 결과에 실제 포함된 카드만 앞면 |
| 리플레이 | 선택 시점의 허용 범위 그대로 |

back/absent의 화면 데이터에는 rank/suit 자체를 넣지 않는다. CSS만으로 숨기지 않는다.
과거 화면에 나중에 공개된 카드를 역으로 채워 넣지 않는다.

### SpeechBubble

각 좌석은 가장 최근의 확정 발언 하나를 표시한다. 입력 중/접수 중인 초안과 구분한다.
긴 발언은 표시 높이를 제한하고 우측 기록에서 전체 내용을 볼 수 있게 한다.
현재 말풍선은 최대 세 줄 높이에서 내부 세로 스크롤로 원문 전체를 읽는다.
키보드로도 스크롤 영역에 포커스할 수 있고, 새 사건의 발언으로 바뀌면 스크롤을 처음으로 돌린다.
고정 높이 슬롯을 사용하며 하단 인간 좌석은 기존보다 64px 아래에 배치한다.
좌우 AI와 중앙 정보의 기준 위치는 유지한다. '기록에서 전체 보기'는
expand-speech(eventId)를 PlayerSeat → PokerTable → GameScreen으로 전달한다.
GameScreen은 EventLog의 해당 사건을 강조하고 스크롤/포커스를 이동한다.
이는 기록 읽기 동작이며 과거 테이블 상태로 이동하는 리플레이와 별개다.
베팅은 말풍선과 분리된 작은 행동 라벨로 표시한다.
말풍선 표시/교체와 엔진의 '발언 없음' 상태는 무관하다.
리플레이의 말풍선은 선택 사건까지의 발언만 사용하고 유휴 모션은 정지한다.

## 4. 컴포넌트와 책임

`App.vue`는 화면 조립, `GameScreen.vue`는 화면 영역과 데이터 전달을 담당한다.
컴포넌트는 props를 읽고 사용자 의도를 emits로 전달한다. 직접 HTTP/SSE를 호출하지 않는다.
화면 계약의 필드 초안은 src/contracts.js에 있다.

| 컴포넌트 | 주요 props | emits / 동작 계약 |
|---|---|---|
| GameSetup | busy, disabled, error | create-game({aiIds,humanPlays}) |
| GameToolbar | sessionSummary, connection, readOnly, canStartNextRound | start-round, close-game, return-live |
| PokerTable | aiSeats, humanSeat, summary, readOnly, motionEnabled | expand-speech(eventId) |
| PlayerSeat | seat, readOnly, motionEnabled | expand-speech(eventId) |
| SlimeAvatar | color, state, motionEnabled, gazeAngle, gazeStrength | 표시 전용 |
| AvatarBody | color | 기본 slot에 눈 배치 |
| AvatarEyes | motionEnabled, gazeAngle, gazeStrength | 시선 위치와 CSS 눈 모션 |
| PlayingCard | card | 표시 전용 |
| SpeechBubble | speech | expand-speech(eventId) |
| TableStatus | summary | 표시 전용 |
| HumanControls | participation, commands, remainingUtterances, deferredSpeech, readOnly | speak({text}), silent, release-speech, bet(bet) |
| BettingControls | allowedBets, raiseOptions, disabled | bet({action,raise_size?}) |
| EventLog | entries, selectedEventId, selectedFrameId, replayAvailable | focusEvent(eventId)로 기록 포커스; seek-frame(frameId) |
| ReplayControls | rounds, mode, selectedRoundId, selectedFrameId, position, frameCount, loading, error | select-round(roundId), step-frame(first/previous/next/last), seek-position(index), return-live, retry |

GameScreen은 사용자 의도를 상위 상태 조립 계층에 전달한다. expand-speech는 기록의 해당
발언을 펼치거나 포커스하는 화면 동작이다. 게임 명령을 새로 만들지 않는다.
ReplayControls는 방향/슬라이더 위치 의도만 전달하고 useReplay가 실제 frameId로 해석한다.
모든 버튼을 각각 별도 컴포넌트로 나누지는 않는다.

GameSetup은 AI 2/3명과 플레이/관전을 로컬 입력으로 보관하고 만들기를 누를 때만 emit한다.
생성 중/비활성화 상태에서는 입력과 submit을 막고 오류 원문을 텍스트로 표시한다.
GameToolbar는 SessionSummary/ConnectionState(contracts.js)의 상태를 표시한다.
첫/다음 라운드 버튼은 gameId, canStartNextRound, created/ready 상태를 모두 확인한다.
진행/정리/종료/오류/명령 처리/리플레이 중에는 시작할 수 없다. 종료는 열린 gameId가 있고
명령 처리/리플레이 중이 아닐 때 가능하다. 서버 연결 상태를 자체적으로 추측하지 않는다.

## 5. 상태와 네트워크 경계

```text
App / GameScreen
  ├─ useGameSession → gameApi / eventStream → 같은 출처의 웹 프록시
  ├─ useReplay → useGameSession의 토큰 비공개 조회 → replayApi → /games/{id}/history
  └─ toTableView → PokerTable / PlayerSeat 등 동일한 표시 컴포넌트
```

- useGameSession: liveState, 연결 상태, 요청 처리 상태, 게임 생성/시작/명령/종료.
- useReplay: 현재 게임의 진행/완료 라운드 목록, 선택 프레임, 이전/다음/처음/마지막, 현재로 복귀.
- toTableView: 서버의 snake_case 상태와 공개 사건을 화면용 TableView로 바꾸는 순수 변환 위치.
- gameApi: 일반 요청. 인증 토큰은 이 계층과 composable에서만 사용한다.
- eventStream: fetch 기반 SSE. Authorization/Last-Event-ID, 분할 수신, named event,
  heartbeat, 재연결과 ResyncRequired를 처리한다. close는 구독만 닫고 게임을 삭제하지 않는다.
- replayApi: /history 목록/라운드 조회 전용. 기존 gameApi의 인증/요청 처리를 재사용한다.

LiveGame은 초기 서버 상태 점검과 저장된 게임 재접속만 수행한다. 게임 생성/라운드 시작은 버튼으로 요청한다.
컴포넌트의 로컬 상태는 입력 초안/기록 펼침 등 화면 동작으로 한정한다.
토큰과 game_id는 sessionStorage에 재접속 정보로 보관하지만
화면 props, 사건 목록, 리플레이 프레임, 로그에 토큰을 넣지 않는다.
서버 재시작 후 게임 복원은 현재 백엔드에서 지원하지 않는다.

## 6. 인간 입력과 버튼

- 참여 여부와 readOnly를 먼저 확인한다. 관전/탈락/리플레이에서는 명령을 보내지 않는다.
- 발언은 자유 텍스트다. game/chat 경로를 사용자에게 선택시키지 않는다. 복합 해석은 서버가 한다.
- 인간의 잡담도 발언 횟수에 포함하며 서버의 남은 기회를 표시한다.
- AI 순수 잡담 응답과 게임 혼합 응답 횟수는 서버가 결정한다. UI에서 다시 판정하지 않는다.
- 폴드는 베팅만 종료한다. 폴드 참가자도 같은 라운드가 dialogue인 동안 남은 발언 기회로
  대화/침묵 선언/보류 발언 재확인을 할 수 있다. 연속 발언 제한과 기존 횟수 계산은 유지한다.
  발언 가능 여부는 observation.can_speak, 베팅 가능 여부는 allowed_bets로 각각 판단한다.
  폴드 카드 비공개와 베팅 완료 즉시 공개 규칙은 유지한다. 공개 후에는 누구도 발언하지 않는다.
- 체크/콜/폴드 및 레이즈는 allowed_bets/raise_options만 사용한다.
- maximum/large/medium/small은 최대/큰/중간/작은으로 표시하고 실제 증가량도 함께 보여준다.
- 칩/콜 비용/한도를 UI에서 계산하지 않는다. 선택 불가능한 크기는 활성화하지 않는다.
- 보류 발언은 보류로 표시하고 인간의 재확인으로만 공개한다.
- request_id는 새 명령마다 새 값, 재전송에는 같은 값이다.
- round_id/basis_event는 항상 liveState에서 만든다. SSE sequence나 과거 프레임 번호를 쓰지 않는다.
- CommandResult와 SSE 양쪽으로 같은 발언을 받더라도 한 번만 표시한다.
- 다음 라운드는 RoundReady와 can_start_next_round를 확인하여 명시적으로 시작한다.

HumanControls는 commands.canSpeak/canDeclareSilent/canRelease와 남은 횟수를 읽고
초안만 로컬에 보관한다. 관전/리플레이/명령 처리/전체 disabled는 공통 차단한다.
발언 소진이나 보류 문장 존재 시 새 발언을 보내지 않지만 베팅 권한과는 분리한다.
Enter는 줄바꿈, Ctrl/Cmd+Enter는 전송이며 IME 조합 중에는 전송하지 않는다.
서버 maxMessageChars가 있으면 입력 상한으로 사용한다. 전송 오류 시 초안을 유지하고,
commands.speechReceipt의 contextKey/status/text가 현재 맥락의 초안과 맞는 성공/보류일 때만 비운다.
commands.contextKey는 게임/라운드가 바뀔 때 변경하여 이전 초안을 초기화한다.

BettingControls는 기본 행동 세 개와 레이즈 네 크기를 보여 주되 서버가 허용한 선택만 켠다.
레이즈 증분은 raiseOptions에 있는 양의 정수만 표시한다. emit은 bet({action}) 또는
bet({action:'raise',raise_size})이며 금액 계산이나 HTTP 요청을 하지 않는다.
GameScreen은 상단/인간 입력 emits를 상위로 전달하고 리플레이 mode의 읽기 전용을 함께 적용한다.

`?preview=1`의 GamePreview는 실제 서비스 대신 useTablePreview를 연결한다. 인원/관전 선택은 예시 좌석에 적용하고,
관전에서는 인간 카드/칩/발언을 없애며 AI 카드는 뒷면만 전달한다. PreviewTools에서 내 차례,
폴드, 발언 소진, 보류, 다음 라운드 대기 예시를 고를 수 있다. 버튼 입력은 안내로 확인할 뿐
게임 명령 접수 성공이나 새 사건/게임 기록으로 만들지 않는다.

기본 LiveGame은 useGameSession의 실제 상태를 연결한다. HTTP 응답과 SSE의 사건은 키로 합치고,
SSE sequence/stream_cursor는 전송 복구에, event_id는 명령 basis_event에 사용한다.
동일 event_id의 RoundReady도 높은 stream_cursor로 새 상태를 반영한다. 오래된 응답과 명령
완료 전 시작된 조회는 최신 상태를 덮어쓰지 못한다. 사건 알림이 몰리면 조회를 합친다.
전송 결과가 불확실하면 다른 명령을 잠그고 같은 request_id/본문으로 재확인한다.
규칙상 rejudge 거절은 갱신 후 사용자가 다시 선택한다. SSE를 닫는 것은 게임 종료와 별개다.

기존 snapshot에 recent_events/is_silent와 rules의 입력/기록 상한을 추가했다.
최근 공개 사건은 현재 라운드 복구용이며 과거 프레임이 아니다. 접속 중인 기록은 한도까지
유지하지만 새로고침 후 이전 모든 라운드의 상태/기록까지 복구하지는 않는다.
구현 함수와 실행/검증 순서는 CONNECTION_GUIDE.md에 정리했다.

## 7. 현재 게임의 사건 선택과 과거 화면 탐색 — 구현 명세

이 절에 따라 사건별 스냅샷 저장/조회와 과거 테이블 표시를 구현했다.
말풍선의 원문 포커스와 기록 클릭에 의한 시점 이동은 별도 동작으로 유지한다.

### 7.1 범위와 보관 기간

- 대상은 현재 game_id의 한 게임이다. 그 게임의 첫 라운드부터 현재까지 탐색한다.
  이전 game_id 목록, 게임 보관함, 내보내기, 자동 재생은 이번 범위에서 제외한다.
- 진행 중인 라운드, 완료된 라운드, 우승이 정해진 게임 모두 확정된 사건까지 조회할 수 있다.
- 서버는 해당 테이블의 메모리에 기록을 유지한다. 새 DB/디스크 저장은 추가하지 않는다.
  기존 RAG 경험 기록/라운드 JSON을 화면 복구 자료로 사용하지 않는다.
- 게임 종료(finished)는 기록을 지우지 않는다. 명시적인 테이블 종료(DELETE), 서버 재시작,
  서버 프로세스 종료로 기록이 사라진다. 새 게임은 별도 game_id로 시작한다.
- 서버에 테이블이 남아 있고 같은 토큰으로 재접속하면 새로고침 후에도 그 게임의
  라운드 목록과 프레임을 다시 조회할 수 있다. 브라우저는 초기에는 현재 화면을 보여준다.
- SSE/recent_events의 event_history_limit과 리플레이 보관은 별개다. SSE 버퍼가 밀려나거나
  Table.events가 다음 라운드에서 비워져도 과거 프레임은 지우지 않는다.
- MVP는 현재 게임의 전체 프레임을 유지한다. 프레임에 전체 대화/프레임 목록을 중첩하지 않고
  당시 표시용 최소 정보만 복사한다. 보존 상한을 나중에 추가하면 삭제 범위를 명시해야 하며
  기존 SSE FIFO 한도를 재사용해 조용히 오래된 장면을 버리지 않는다.

### 7.2 사용자 동작과 화면 의미

1. 우측 기록의 발언 또는 게임 사건을 클릭한다. 프레임이 있는 항목만 시점 이동할 수 있다.
2. 해당 사건의 처리가 끝난 직후의 테이블로 화면을 바꾼다. 클릭한 발언이 말풍선에 보이고,
   카드/칩/판돈/차례/폴드/최근 행동도 그 시점의 값이어야 한다.
3. '과거 기록 · 라운드 N · 사건 #M' 표시와 선택 기록 강조를 유지한다.
4. 라운드 선택, 처음/이전/다음/마지막, 선택 라운드 안의 슬라이더로 이동할 수 있다.
   슬라이더는 프레임 배열 위치를 사용하고 event_id를 연속 인덱스로 가정하지 않는다.
5. '현재로 돌아가기'를 누르면 그동안 갱신된 최신 테이블로 돌아간다.

게임 엔진은 되감거나 일시정지하지 않는다. 과거를 보는 동안 AI 판단/베팅/공개와 SSE 수신은
계속 진행한다. 새 사건 때문에 선택한 프레임이나 라운드를 자동으로 바꾸지 않고,
'현재에 새 사건 N개' 같은 안내만 갱신한다. 최신 프레임에 도달해도 명시적으로 복귀하기
전에는 replay 모드를 유지한다.

과거를 보는 동안 생성/발언/침묵 선언/보류 발언 공개/베팅/라운드 시작/테이블 종료와
접수 불명확 요청의 재전송 등 모든 게임 변경 명령을 막는다. 연결 재확인과 기록 조회는 가능하다.
진입 전에 이미 전송한 명령을 취소하거나 다시 보내지는 않고 그 결과는 liveState에 반영한다.
컴포넌트 disabled뿐 아니라 LiveGame의 명령 전달 경계에서도 replay 모드를 검사한다.

상단의 서버 연결/실제 게임 진행 상태는 최신 정보를 유지하고 '현재 상태'임을 구분한다.
테이블과 과거 발언 잔여 횟수 등 선택 장면의 표시는 프레임에서 가져온다. 입력 초안/접수 결과는
실시간 맥락에 남겨 두며 과거 round_id/event_id로 명령을 구성하지 않는다.

### 7.3 프레임을 저장하는 시점

프레임은 서버의 Table에서 해당 상태 변경이 끝난 지점에, 기존 동기 처리/락 안에서 복사한다.
runner._dispatch/on_event나 SSE 수신 후 GET /games/{id}로 저장하지 않는다. 그 시점에는
이미 다음 사건까지 진행되었을 수 있다.

| 사건 | 프레임에 반영된 상태 |
|---|---|
| RoundStarted | 카드 배분/참가비 차감/현재 라운드 초기화가 끝난 상태 |
| Speech | 발언 본문/횟수/최근 발언자/잡담 응답 상태 반영 후 |
| PlayerSilent | 발언 없음 상태 반영 후 |
| BetPlaced | 해당 베팅/폴드/기여금/다음 차례 반영 후, 후속 자동 행동이나 정산 전 |
| AgentFailed | 오류 표시와 보류 정리 후, 후속 자동 체크/폴드 전 |
| RoundEnded | 판돈 지급/이월/탈락/우승/공개 카드와 종료 단계 반영 후 |

_publish_speech는 _emit('Speech', _capture=False)로 사건 ID를 먼저 만든다.
Conversation.add와 잡담 응답 상태를 반영한 뒤 capture를 호출한다. 다른 사건은 _emit에서
공통 capture를 호출한다. 발언 횟수/말풍선을 반영하기 전에 저장하지 않는다.
한 명령이 BetPlaced → 자동 BetPlaced → RoundEnded를 만들면 각 중간 상태를 따로 저장한다.

거절/중복 요청/아직 공개하지 않은 보류 발언, heartbeat, 모델 추론/메모 저장에는 프레임을
추가하지 않는다. RoundReady/MemoryPublishFailed/RunFailed/GameClosed 같은 서비스 알림은
기록으로 남길 수 있지만 이번 MVP에서는 시점 이동 대상에 넣지 않는다.
공개 이후 서비스 회고가 끝났는지는 실시간 상단 상태로 확인한다.

프레임은 생성 시 깊은 복사하고 조회 시에도 내부 객체를 노출하지 않는다. 이후의 라운드,
메모 수정, 게임 결과가 저장된 과거 스냅샷을 변경하면 안 된다.

### 7.4 저장 계약과 정보 범위

서버 전송 계약은 다음과 같으며 contracts.js의 ReplayFrame도 같은 필드를 사용한다.
화면용 TableView는 프런트에서 계산한다.

```text
ReplayFrame
  frame_id        서버가 부여하는 불투명한 게임 내 고유 ID
  game_id
  round_id
  event_id        해당 Table 사건 번호
  event           해당 공개 사건 원문
  snapshot        그 사건 직후의 허용된 화면 상태
```

snapshot에는 당시 game_id/round_id/event_id/phase, 생성 순서의 players, human_id,
폴드 참가자, pot/current_bet/betting_turn, 공개 가능한 visible_cards, 당시 result/우승 정보,
관찰 시점의 perspective(player 또는 spectator), 인간의 표시용 참여 상태/남은 발언 횟수를 둔다.
각 참가자의 최근 확정 발언과 최근 베팅 사건은 display_events에 보관한다. 전체 대화 대신
표시에 필요한 최신 발언/베팅만 유지하고 event_id 순서로 정렬한다.

- 관점은 생성 시 정해진 인간/AI-only 관전과 그 시점의 참가 상태에서 서버가 결정한다.
  클라이언트가 다른 에이전트나 전지적 관점을 지정하는 파라미터는 받지 않는다.
- 카드 정보는 기존 observe/현재 상태 API와 같은 공개 범위만 저장한다. 숨긴 자기 카드,
  폴드 카드의 앞면, 개인 메모/추론/시스템 프롬프트/토큰은 프레임에 넣지 않는다.
- AI-only 관전은 공개 전 앞면이 없다. 인간 참가 중에는 당시 허용된 상대 카드만 들어간다.
  종료 프레임에서도 실제 공개 대상 카드만 넣는다.
- 공개 후 카드를 알아도 이전 프레임을 수정하거나 liveState/결과 카드로 보충하지 않는다.
- sequence/stream_cursor는 실시간 재연결 값이다. 프레임 ID나 탐색 위치로 사용하지 않으며
  사건 확정 시 아직 없는 sequence를 저장하기 위해 프레임 저장을 늦추지 않는다.
- Vue 컴포넌트/CSS 색상/배치 코드를 서버에 옮기지 않는다. perspective와 display_events를
  읽는 리플레이 변환을 기존 toTableView에 연결해 같은 표시 컴포넌트를 사용한다.
  생략된 전체 Observation을 가짜 빈 observation 객체로 대체해 카드 권한을 우회하지 않는다.

agents/replay_history.py의 ReplayHistory 객체가 프레임, 라운드 인덱스, event_id→frame_id
인덱스와 최근 표시 사건을 관리하도록 한다. Table은 저장 시점과 허용 상태 제공을 담당하고,
GameSession은 인증된 테이블의 기록 조회만 중계한다. RAG/MCP 저장과 분리한다.

### 7.5 조회와 기존 SSE 연결

구현한 API는 아래 두 조회 형태로 한정한다.

| 요청 | 응답 계약 |
|---|---|
| GET /games/{id}/history | game_id, 라운드 목록(round_id/completed/frame_count/first_frame_id/last_frame_id), latest_frame_id |
| GET /games/{id}/history?round_id=N | game_id, round_id, completed, 해당 라운드의 latest_frame_id, event_id 순서의 frames 배열 |

진행 중인 라운드도 반환한다. 조회 중 새 사건이 생겨도 한 응답은 복사 시점까지의 일관된
프레임 목록이다. 캐시 갱신 시 frame_id로 합치고 기존 프레임 내용은 변경하지 않는다.
라운드 단위 조회로 충분하므로 MVP에는 별도 프레임 상세 API나 페이지네이션을 추가하지 않는다.
기록 없는 라운드는 404, 없어진 테이블/유효하지 않은 토큰은 기존 인증/조회 규칙을 따른다.
테이블이 아직 라운드를 시작하지 않았다면 라운드 목록은 빈 배열이다.

기존 Bearer 세션 검증을 재사용하고 같은 game_id의 소유자에게만 해당 관점의 프레임을 반환한다.
웹 프록시는 기존 /games 일반 중계를 재사용한다. SSE 연결/재시도 규약은 바꾸지 않는다.

GameSession이 공개 사건을 SSE journal 또는 recent_events 응답으로 복사할 때 저장된
frame_id를 붙인다. 원본 Table.events/라운드 아카이브에 frame_id를 끼워 넣어 RAG/MCP 기록
계약을 바꾸지 않는다. 저장과 frame_id 등록은 runner가 사건을 전달하기 전에 끝나 있어야 한다.
publicEvents.toEventEntries는 이 링크를 EventEntry.frameId로 전달한다.

재접속 시 /history로 현재 게임의 라운드를 찾고 필요한 라운드를 조회한다. 기존 live 기록
버퍼에서 빠진 라운드도 라운드 선택 후 해당 프레임의 event 목록으로 읽을 수 있어야 한다.
프레임이 없는 알림/예전 서버의 사건은 클릭을 비활성화하고 현재 상태로 대체하지 않는다.

### 7.6 프런트 상태와 파일별 연결

```text
useGameSession.liveState       최신 서버 상태, SSE/명령/접수 결과의 기준
useReplay                     mode, 라운드 목록, 프레임 캐시, selectedFrameId
live TableView                liveState + 실시간 공개 사건으로 계산
replay TableView              선택 snapshot + 그 snapshot.display_events로 계산
LiveGame.displayView          mode에 따라 위 둘 중 하나를 전달
```

- replayApi.js: 토큰을 가진 통신 계층에서 listReplayRounds/getReplayFrames 구현.
- useReplay.js: 현재 game_id의 라운드/프레임 캐시, selectRound/seekFrame/이전·다음/복귀 관리.
  선택 사건이 캐시에 없으면 그 round_id를 조회한 뒤 정확한 frame_id로 이동한다.
- LiveGame.vue: 실제 상태와 표시 상태를 분리하고 모든 게임 변경 이벤트를 replay에서 차단한다.
  새로운 game_id/테이블 삭제/접속 상실 확정 시 선택·캐시·진행 중 조회를 정리한다.
- GameScreen/ReplayControls: 읽기 전용·선택 위치·과거 표시와 탐색 의도만 전달한다.
  재생 중 눈 모션은 멈춘다. 현재로 돌아가면 최신 화면과 입력 권한을 다시 사용한다.
- EventLog: frameId가 있는 기록의 행/버튼 클릭으로 seek-frame을 전달한다. 선택 표시를
  유지하며 선택 라운드의 뒤쪽 기록도 탐색용으로 볼 수 있다. 뒤쪽 발언을 선택 장면의
  말풍선에 미리 반영하지 않는다.
- SpeechBubble의 '기록에서 전체 보기': 기존 원문 포커스 동작을 유지한다. 시점 이동은
  EventLog의 기록 선택으로 구분해 불필요하게 보기 모드를 바꾸지 않는다.
- toTableView/contracts: wire snapshot/화면 계약을 정리하고 생성 순서의 좌석/색을 재사용한다.
  나중 상태로 카드/칩/말풍선을 보충하지 않는다.

선택 요청 A 뒤에 B를 눌렀다면 늦게 도착한 A 응답이 B를 덮어쓰지 못하게 한다.
다른 라운드를 읽는 동안에는 이전 표시를 유지하고 '기록 불러오는 중'과 명령 차단을 적용한다.
실패 시 임의의 프레임을 표시하지 않고 재조회/현재 복귀를 제공한다. SSE 단절은 저장된
과거 화면을 지우지 않지만 테이블 삭제/서버 재시작으로 404가 확인되면 캐시를 정리한다.
리플레이 중 프레임 조회/선택/복귀는 어떤 LLM/MCP/게임 변경 요청도 발생시키지 않는다.

### 7.7 구현 순서와 완료 조건

1. ReplayHistory와 Table의 정확한 저장 지점 구현. 공개 정보/깊은 복사를 먼저 검증한다.
2. 인증된 /history 조회와 SSE/recent_events의 frame_id 연결. 기존 실시간 흐름을 회귀 검사한다.
3. replayApi/useReplay와 LiveGame의 표시 분리 및 명령 차단 구현.
4. EventLog 클릭과 ReplayControls를 기존 테이블/캐릭터에 연결한다.

완료 검증에는 다음을 포함한다.

- Speech를 클릭하면 그 문장/남은 발언 횟수, BetPlaced는 당시 칩/판돈/차례를 보여준다.
- 마지막 베팅 직후와 RoundEnded 정산 직후가 다른 프레임으로 조회된다.
- 공개 전/폴드/AI-only 관전 카드가 나중 결과로 바뀌지 않는다. 토큰/메모 원문이 없다.
- 여러 라운드 이후에도 초기 라운드를 선택할 수 있고, 새로고침 후 같은 테이블에서 재조회된다.
- 과거를 보는 동안 실제 게임과 SSE는 진행하고 선택 화면은 고정된다. 복귀 시 최신 상태가 보인다.
- 과거에서 모든 게임 변경 경로를 차단하고 실제 LLM/MCP 호출 횟수가 늘지 않는다.
- 게임 finished 후 기록은 유지되고 명시적 DELETE/서버 재시작 이후에는 조회가 불가능하다.
- 빠른 연속 선택, 조회 실패, SSE 재접속, 중복 사건에서 프레임이 섞이거나 중복되지 않는다.

## 8. 후속 구현 순서

1. PlayerSeat/SlimeAvatar/PlayingCard/SpeechBubble, 고정 좌석/중앙 정보/기록 표시는 구현 완료.
2. GameSetup/GameToolbar/HumanControls/BettingControls의 입력과 emits, 미리보기 연결은 구현 완료.
3. 실제 서버 관찰 → TableView 변환과 일반 API/SSE/실시간 상태, 인간 명령 연결은 구현 완료(fake 검증).
4. 7절의 현재 게임 메모리 기록/정확한 사건별 저장/인증된 history 조회 구현 완료.
5. 기록 클릭과 리플레이 탐색, 실시간 상태 분리/명령 차단을 같은 표시 컴포넌트에 연결 완료.

각 단계가 끝나도 실제 모델을 돌리지 않고 fake 백엔드로 화면/입력을 점검할 수 있게 한다.
