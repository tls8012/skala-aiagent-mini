# 데스크톱 포커 화면 명세

## 1. 범위와 현재 구현 수준

Vue SFC 폴더/컴포넌트/데이터 계약을 바탕으로 참가자와 테이블 표시를 구현했다.
캐릭터, 앞면/뒷면/없는 카드, 말풍선, 칩/차례/상태, 고정 좌석, 중앙 현황과 스크롤 기록이 동작한다.
App은 preview/tablePreview.js의 예시를 전달하며 화면에 미리보기임을 표시한다.
게임 설정/상단 상태·명령 버튼과 인간 발언/침묵/보류 확인/베팅 입력 컴포넌트도 구현했다.
실제 게임 상태 변환, API/SSE와 입력 명령의 서버 연결, 리플레이는 아직 구현하지 않았다.
미리보기 전용 상태 조립은 preview/useTablePreview.js에 분리하고 PreviewTools에서 입력 상태를 바꿔 본다.
서비스와 composable의 TODO 함수는 호출 시 명시적으로 오류를 낸다. 가짜 성공/가짜 기록을 반환하지 않는다.

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
| ReplayControls | rounds, selectedRoundId, selectedFrameId, position, frameCount, loading, error | select-round(roundId), step-frame(first/previous/next/last), seek-position(index), return-live |

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
  ├─ useReplay → replayApi → 기록 조회(백엔드 선행 작업 필요)
  └─ toTableView → PokerTable / PlayerSeat 등 동일한 표시 컴포넌트
```

- useGameSession: liveState, 연결 상태, 요청 처리 상태, 게임 생성/시작/명령/종료.
- useReplay: 완료 라운드 목록, 선택 프레임, 이전/다음/처음/마지막, 현재로 복귀.
- toTableView: 서버의 snake_case 상태와 공개 사건을 화면용 TableView로 바꾸는 순수 변환 위치.
- gameApi: 일반 요청. 인증 토큰은 이 계층과 composable에서만 사용한다.
- eventStream: fetch 기반 SSE. Authorization/Last-Event-ID, 분할 수신, named event,
  heartbeat, 재연결과 ResyncRequired를 처리한다. close는 구독만 닫고 게임을 삭제하지 않는다.
- replayApi: 조회 전용. 현재 서버에는 정확한 사건별 과거 상태 API가 없다.

초기 스켈레톤은 이 함수들을 호출하지 않는다. API가 연결되기 전 자동 게임 생성이나 LLM 호출은 없다.
후속 구현에서 컴포넌트의 로컬 상태는 입력 초안/기록 펼침 등 화면 동작으로 한정한다.
토큰과 game_id는 로컬 MVP에서 sessionStorage로 재접속할 수 있게 보관할 수 있지만
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

현재 App은 실제 서비스 대신 useTablePreview를 연결한다. 인원/관전 선택은 예시 좌석에 적용하고,
관전에서는 인간 카드/칩/발언을 없애며 AI 카드는 뒷면만 전달한다. PreviewTools에서 내 차례,
폴드, 발언 소진, 보류, 다음 라운드 대기 예시를 고를 수 있다. 버튼 입력은 안내로 확인할 뿐
게임 명령 접수 성공이나 새 사건/게임 기록으로 만들지 않는다. 실제 네트워크 연결은 다음 단계다.

## 7. 리플레이: 필수 요구와 아직 없는 백엔드

목표는 느린 로컬 실행 결과를 다시 돌리지 않고 라운드/게임 전체의 이전 상태를 탐색하는 것이다.
MVP 조작은 라운드 선택, 처음/이전/다음/마지막, 슬라이더, 기록 클릭, 현재로 돌아가기다.
자동 재생 속도나 동영상 저장은 이번 범위가 아니다.

실시간 원본과 표시 커서는 별개다.

```text
liveState                       실제 서버의 현재 상태
frames + selectedFrameId         완료 라운드의 과거 관찰
mode                            live 또는 replay
TableView                       선택한 모드의 읽기 전용 표시 데이터
```

프레임 이동은 엔진 상태/메모를 바꾸거나 LLM을 다시 호출하지 않는다.
리플레이 중에는 입력, 베팅, 다음 라운드 시작을 비활성화한다. 명시적으로 현재로 복귀한 후
다음 라운드를 시작한다. 라운드 종료 검토 구간과 게임 종료 후에 탐색할 수 있게 한다.
한 프레임에서 카드/칩/판돈/폴드/차례/말풍선/대화의 시점은 서로 같아야 한다.

현재 서버의 SSE는 공개 사건이고 GET /games/{id}는 현재 상태다. SSE를 받은 뒤 현재 상태를
조회해 저장하면 여러 사건이 이미 진행된 상태일 수 있어 정확한 과거 화면을 보장할 수 없다.
특히 관찰 카드와 당시 칩/발언 상태를 기존 공개 사건만으로 모두 복원할 수 있다고 가정하면 안 된다.

따라서 후속 백엔드 작업으로 다음이 필요하다.

1. 사건 확정 시점에 해당 인간/관전자의 허용된 관찰과 최근 말풍선 상태를 저장.
   runner의 지연된 전달 callback에서 현재 상태를 찍는 방식으로 대체하지 않는다.
2. 완료 라운드별 목록과 사건별 불변 스냅샷 조회.
   제안 경로: GET /games/{id}/history, GET /games/{id}/history?round_id=1.
   **이 경로는 현재 구현되어 있지 않다.**
3. 프레임 계약: frame_id, round_id, event_id, sequence(선택), event, snapshot.
   UI에서는 snapshot을 같은 TableView로 변환하고 프레임 ID로 탐색한다.
4. 인간 관찰 기록은 공개 RAG 경험과 구분하여 저장하고 게임 토큰으로 접근을 제한.
   숨겨진 자기 카드/개인 메모/프롬프트를 기록에 추가하지 않는다.
5. 페이지를 나중에 열어도 완료 기록을 조회할 수 있도록 저장/보존 범위를 확정.
   현재 SSE 버퍼나 브라우저 수신 기록만으로 이 요구가 완성되었다고 표시하지 않는다.

현재 replayApi/useReplay는 이 선행 작업을 드러내는 스텁이다.
백엔드 구현 전에는 리플레이를 사용 가능하다고 표시하거나 가짜 과거 상태를 생성하지 않는다.

## 8. 후속 구현 순서

1. PlayerSeat/SlimeAvatar/PlayingCard/SpeechBubble, 고정 좌석/중앙 정보/기록 표시는 구현 완료.
2. GameSetup/GameToolbar/HumanControls/BettingControls의 입력과 emits, 미리보기 연결은 구현 완료.
3. 실제 서버 관찰 → TableView 변환과 일반 API/SSE/실시간 상태, 인간 명령 연결.
4. 사건별 관찰 저장/조회 백엔드 구현.
5. 리플레이 탐색을 같은 표시 컴포넌트에 연결.

각 단계가 끝나도 실제 모델을 돌리지 않고 fake 백엔드로 화면/입력을 점검할 수 있게 한다.
