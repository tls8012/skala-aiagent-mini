# 포커 Vue 프런트엔드 스켈레톤

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
미리보기 데이터는 `src/preview/tablePreview.js`에 분리했다. 화면에도 예시임을 표시한다.
HTTP/SSE, 실제 게임 상태 변환, 인간 입력/베팅, 리플레이 등은 TODO다.
초기 화면을 열어도 게임 생성이나 LLM 호출은 발생하지 않는다.

## 읽는 순서

1. [UI_SPEC.md](UI_SPEC.md): 요구사항, 컴포넌트 계약, 리플레이 선행 작업.
2. [src/App.vue](src/App.vue), [src/views/GameScreen.vue](src/views/GameScreen.vue): 조립 구조.
3. [src/components/table/PlayerSeat.vue](src/components/table/PlayerSeat.vue): 캐릭터/카드/말풍선 조립.
4. [src/contracts.js](src/contracts.js): 화면 전용 데이터 계약 초안.
5. src/composables와 src/services: 후속 상태 관리와 연결 위치. 지금은 명시적인 TODO 스텁이다.

## 실행

```sh
cd web
npm ci
npm run dev
```

개발 화면은 http://127.0.0.1:5173 이다. API를 붙일 때는 기존 두 Python 서버도 실행한다.
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
`npm test`는 실제 Vue SFC를 HTML로 렌더링해 카드 비공개/폴드/관전과 발언 이스케이프를 확인한다.
브라우저의 배치/클릭/애니메이션 확인을 대체하는 검사는 아니다.

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
    contracts.js              UI 데이터 계약(JSDoc)
    views/GameScreen.vue      테이블/기록/조작/리플레이 영역 조립
    components/
      GameSetup.vue           AI 수와 인간 참여 설정
      GameToolbar.vue         게임 상태와 라운드 시작/종료
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
      replayApi.js            아직 없는 기록 API의 연결 자리
    adapters/toTableView.js   서버 관찰 → 동일한 화면 계약
    styles/
      tokens.css              공통 값
      layout.css              전역 데스크톱 기준과 공통 자리 표시
```

화면 이동은 아직 한 화면이므로 Router/Pinia는 추가하지 않았다.
서비스와 상태는 화면 컴포넌트 안에 넣지 않는다. 각 컴포넌트 고유 스타일은 해당 .vue의 scoped style에 둔다.
Vue/Vite 구성의 기준: [Vue 공식 Quick Start](https://vuejs.org/guide/quick-start.html).
