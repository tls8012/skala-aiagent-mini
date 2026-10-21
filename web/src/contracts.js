/** 화면 계약 초안. API 응답은 adapters/toTableView에서 변환하며 토큰은 포함하지 않는다. */

/**
 * @typedef {Object} CardView
 * @property {'face'|'back'|'absent'} visibility
 * @property {number|null} rank face일 때만 2..14. back/absent는 null.
 * @property {'S'|'D'|'H'|'C'|null} suit face일 때만 값이 존재한다.
 */

/**
 * @typedef {Object} SpeechView
 * @property {number} eventId
 * @property {number} roundId
 * @property {string} playerId
 * @property {'game'|'chat'|'mixed'} channel
 * @property {string} text 확정된 공개 발언 원문.
 */

/**
 * @typedef {Object} SeatView
 * @property {string} key 인간 자리는 참여 여부와 무관하게 'human-anchor'.
 * @property {string|null} playerId 관전 앵커는 null이며 서버 참가자로 추가하지 않는다.
 * @property {string} label
 * @property {'ai'|'human'} role
 * @property {'playing'|'spectating'} participation
 * @property {string} position 게임 생성 시 고정하는 좌석 슬롯. 라운드 순서와 분리.
 * @property {string} color
 * @property {number} [gazeAngle] 눈 방향(도). 오른쪽 0, 아래 90, 왼쪽 180, 위 -90.
 * @property {number} [gazeStrength] 중심에서 눈 이동 정도(0..1). 0이면 정면.
 * @property {number|null} chips 관전자에게 가짜 칩을 부여하지 않는다.
 * @property {'active'|'folded'|'eliminated'|'failed'|'spectating'} state
 * @property {boolean} isBettingTurn
 * @property {CardView} card
 * @property {SpeechView|null} speech 해당 시점 이전의 가장 최근 확정 발언.
 * @property {string|null} lastActionLabel 서버 확정 사건을 표시한 행동 라벨.
 */

/**
 * @typedef {Object} TableView
 * @property {SeatView[]} aiSeats
 * @property {SeatView} humanSeat 관전 중에도 반드시 존재하는 UI 앵커.
 * @property {{roundId:number, phase:string, pot:number, currentBet:number, bettingTurn:string|null}} summary
 */

/**
 * @typedef {Object} SessionSummary
 * @property {string|null} gameId
 * @property {'created'|'running'|'summarizing'|'ready'|'finished'|'closed'|'failed'} status
 * @property {number} roundId
 * @property {'playing'|'spectating'} participation
 * @property {boolean} pending 상단 명령 처리 중.
 * @property {string|null} [gameWinner]
 * @property {string|null} [error]
 */

/**
 * @typedef {Object} ConnectionState
 * @property {'idle'|'connecting'|'connected'|'reconnecting'|'disconnected'|'error'} status
 * @property {string|null} [error]
 */

/**
 * @typedef {Object} CommandAvailability
 * @property {boolean} disabled
 * @property {string|null} disabledReason
 * @property {boolean} pending
 * @property {boolean} canSpeak
 * @property {boolean} canDeclareSilent
 * @property {boolean} canRelease
 * @property {boolean} [isSilent] 서버가 확정한 발언 없음 상태.
 * @property {string} [contextKey] 게임/라운드 전환 시 변경. 입력 초안 초기화에 사용.
 * @property {number} [maxMessageChars] 서버 설정으로 받은 입력 길이 상한.
 * @property {string|null} [error] 명령 접수 오류.
 * @property {{id:string, contextKey:string, status:'accepted'|'deferred'|'rejected', text:string}} [speechReceipt] 접수 결과. 같은 맥락의 성공한 원문과 일치할 때만 초안을 비운다.
 * @property {string[]} allowedBets
 * @property {Object<string, number>} raiseOptions 서버가 계산한 이름→증가량. UI에서 재계산하지 않는다.
 */

/**
 * @typedef {Object} EventEntry
 * @property {string} id
 * @property {number} roundId
 * @property {number|null} eventId 게임 사건 번호. 서버 수명 사건에는 없을 수 있다.
 * @property {number|null} sequence SSE 전송 커서. basis_event로 사용하지 않는다.
 * @property {string} type
 * @property {string|null} playerId
 * @property {string|null} text
 * @property {string|null} frameId 서버 기록에 대응할 때만 존재한다.
 */

/**
 * @typedef {Object} ReplayFrame
 * @property {string} frame_id 불투명한 ID. 사건/전송 커서/배열 인덱스와 혼용하지 않는다.
 * @property {string} game_id
 * @property {number} round_id
 * @property {number} event_id
 * @property {Object} event 해당 프레임을 만든 원본 공개 사건. SSE sequence는 없다.
 * @property {Object} snapshot 해당 사건 직후 허용된 화면 상태. toTableView로 변환한다.
 * @property {'player'|'spectator'} snapshot.perspective
 * @property {Object} snapshot.visible_cards 그때 허용된 카드만 포함한다.
 * @property {Object[]} snapshot.display_events 플레이어별 최신 Speech/BetPlaced만 포함한다.
 * @property {{participation:string, remaining_utterances:number}} snapshot.human_display
 */

/**
 * @typedef {Object} GameApiContext
 * @property {string} gameId
 * @property {string} accessToken services/composables 전용. 컴포넌트 props/emits/기록에 넣지 않는다.
 */

export {};
