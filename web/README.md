# 포커 Vue 프런트엔드

Vue 3 + Vite, JavaScript와 `<script setup>` 기반이다.
캐릭터/카드/말풍선/입력/기록은 각각 `.vue` 컴포넌트로 나눈다.
App.vue는 조립만 맡는다. CDN용 JS 컴포넌트는 제거했다.

현재는 **테이블과 참가자 표시 컴포넌트**를 구현했다.
코랄 A는 오른쪽, 파랑 B는 위, 보라 C는 왼쪽, 검정 인간 캐릭터는 아래에 배치한다.
캐릭터는 CSS 원형 몸통과 알약 모양 눈 두 개로 구성하며, 눈의 작은 상하 움직임과 느린 깜빡임이 있다.
`<SlimeAvatar :gaze-angle="180" :gaze-strength="1" />`처럼 시선을 바꿀 수 있다.
각도는 오른쪽 0°, 아래 90°, 왼쪽 180°, 위 -90°다. strength=0이면 정면을 본다.
카드 앞면/뒷면/없음, 최근 말풍선, 칩/차례/폴드, 중앙 판돈과 스크롤 기록을 표시한다.
말풍선의 '기록에서 전체 보기'는 우측 기록의 해당 발언으로 이동한다.
기본 화면은 실제 서버에 연결한다. `?preview=1`의 예시 데이터는 `src/preview/tablePreview.js`에 분리했다.
게임 설정/상단 상태와 시작·종료 버튼, 인간 발언/침묵/보류 확인/베팅 컴포넌트도 구현했다.
실제 화면은 생성 → 첫 라운드 시작 → 발언/베팅 → 공개 → 다음 라운드/종료 흐름이다.
SSE 알림과 HTTP 상태 조회, 재연결, 한 탭의 sessionStorage 재접속, 동일 요청 재확인을 지원한다.
예시 화면에서는 '입력 상태 예시'로 폴드/발언 소진/보류/다음 라운드를 확인하고 실제 게임은 실행하지 않는다.
현재 게임의 기록을 클릭하면 그 사건 직후의 테이블로 이동한다. 라운드 선택, 처음/이전/다음/마지막,
슬라이더와 '현재로 돌아가기'를 지원한다. 과거를 보는 동안 게임/SSE는 계속 진행하고 입력은 차단한다.
기록은 서버 메모리에 보관하며 같은 테이블로 새로고침하면 다시 조회한다. DELETE/서버 재시작 시 사라진다.
페이지를 열면 새 게임을 자동 생성하지 않고 저장된 세션만 복구한다. 메모리 보관/API/화면 분리 계약은
[UI_SPEC.md 7절](UI_SPEC.md#7-현재-게임의-사건-선택과-과거-화면-탐색--구현-명세)에 정리했다.

## 읽는 순서

1. [UI_SPEC.md](UI_SPEC.md): 요구사항, 컴포넌트 계약, 리플레이 구현 계약.
2. [CONNECTION_GUIDE.md](CONNECTION_GUIDE.md): 실제 연결 흐름/함수 설명/실행/검증.
3. [src/App.vue](src/App.vue), [src/views/LiveGame.vue](src/views/LiveGame.vue): 화면과 세션 조립.
4. [src/components/table/PlayerSeat.vue](src/components/table/PlayerSeat.vue): 캐릭터/카드/말풍선 조립.
5. [src/contracts.js](src/contracts.js): 화면 전용 데이터 계약.

## 실행

```sh
cd web
npm ci
npm run dev
```

개발 화면은 http://127.0.0.1:5173 이다. 프로젝트 루트의 별도 터미널에서 기존 두 Python 서버도 실행한다.

```sh
LLM_BACKEND=fake .venv/bin/python server.py
.venv/bin/python web_demo_server.py
```

기존 서버 프로세스가 실행 중이었다면 새 history API가 반영되도록 재시작한다.
실제 모델로 실행할 때는 backend 서버를 `LLM_BACKEND=ollama`로 시작한다. Ollama/MCP 구성은 기존 코드를 재사용한다.
Vite는 `/games`와 `/health`만 기존 웹 프록시 8600으로 중계한다.
API와 SSE의 인증/실행 로직을 개발 서버에 다시 구현하지 않는다.

```text
개발: 브라우저 5173 → Vite 프록시 → 웹 프록시 8600 → 백엔드 8100
빌드: 브라우저 8600 → 웹 프록시(HTML/assets + API/SSE) → 백엔드 8100
```

```sh
npm run build
npm test
npm run preview
```

빌드 결과는 web/dist에 생성된다. Python 웹 프록시의 `/`와 `/assets`가 이를 제공한다.
`preview`는 빌드 산출물 확인용이다. dist와 node_modules는 버전 관리에서 제외한다.
Node 요구사항은 package.json의 engines를 따른다. 패키지 버전은 lockfile로 고정한다.
`npm test`는 HTTP/SSE/상태 경쟁/요청 재확인/카드 비공개에 대한 Node 테스트도 실행한다.
`npm test`는 실제 Vue SFC를 HTML로 렌더링해 카드 비공개/폴드/관전과 발언 이스케이프를 확인한다.
설정/상단 버튼 상태, 폴드 후 발언과 발언 소진 후 베팅, 관전/리플레이/처리 중 차단,
보류 발언 표시 및 관전 미리보기의 카드 비공개도 검사한다.
브라우저의 배치/클릭/애니메이션 확인을 대체하는 검사는 아니다.
프로젝트 루트의 `.venv/bin/python tests/run_web_integration.py`는 임시 fake 서버와 실제 웹 프록시로
인간/AI-only 라운드, 과거 화면 고정 중 실제 진행, 이전 라운드 재접속과 종료 정리를 검증한다.
실제 모델은 호출하지 않는다.

## 폴더

```text
web/
  index.html                  Vite HTML 진입점
  package.json / package-lock.json
  vite.config.js              Vue 플러그인과 개발용 API 프록시
  UI_SPEC.md
  src/
    main.js                   Vue 마운트
    App.vue                   루트 조립
    preview/tablePreview.js   UI 개발 전용 카드/대화 예시
    preview/useTablePreview.js  설정/입력 상태 예시와 버튼 안내 조립
    preview/PreviewTools.vue  예시 상태 선택과 미리보기 안내
    contracts.js              UI 데이터 계약(JSDoc)
    views/GameScreen.vue      테이블/기록/조작/리플레이 영역 조립
    views/LiveGame.vue        실제 세션과 컴포넌트 이벤트 연결
    views/GamePreview.vue     네트워크 없는 화면 예시
    components/
      GameSetup.vue           AI 수와 인간 참여 설정
      GameToolbar.vue         게임 상태와 라운드 시작/종료
      SessionNotice.vue       서버 모드/오류/재연결/동일 요청 재확인
      table/
        PokerTable.vue        고정 좌석 배치
        PlayerSeat.vue        한 참가자의 표현을 조립
        SlimeAvatar.vue       색깔별 둥근 캐릭터와 눈
        AvatarBody.vue        CSS 원형 몸통과 색상
        AvatarEyes.vue        알약 눈 두 개와 상하/깜빡임 모션
        PlayingCard.vue       앞면/뒷면/없음
        SpeechBubble.vue      최근 확정 발언
        TableStatus.vue       판돈/베팅/단계
      controls/
        HumanControls.vue     인간 발언/침묵/보류 발언
        BettingControls.vue   서버가 준 베팅 선택지
      history/
        EventLog.vue          스크롤 기록과 시점 선택
        ReplayControls.vue    라운드/프레임 탐색
    composables/
      useGameSession.js       실시간 원본 상태와 명령 수명
      useReplay.js            과거 프레임과 화면 커서
    services/
      gameApi.js              일반 HTTP
      eventStream.js          SSE 연결/해제
      sseParser.js            분할 SSE 줄/프레임 파싱
      sessionStorage.js       한 탭의 재접속 정보
      replayApi.js            기록 목록/라운드 조회, 기존 gameApi 재사용
    adapters/toTableView.js   서버 관찰 → 동일한 화면 계약
    adapters/toCommandState.js 발언과 베팅 권한 분리
    adapters/publicEvents.js 사건 중복 제거/기록 변환
    styles/
      tokens.css              공통 값
      layout.css              전역 데스크톱 기준과 공통 자리 표시
```

화면 이동은 아직 한 화면이므로 Router/Pinia는 추가하지 않았다.
서비스와 상태는 화면 컴포넌트 안에 넣지 않는다. 각 컴포넌트 고유 스타일은 해당 .vue의 scoped style에 둔다.
Vue/Vite 구성의 기준: [Vue 공식 Quick Start](https://vuejs.org/guide/quick-start.html).
