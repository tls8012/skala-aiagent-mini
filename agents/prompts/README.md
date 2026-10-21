# 프롬프트 템플릿

실제 실행 경로의 시스템 지시문을 UTF-8 `.txt` 파일로 관리한다.
`render_prompt(name, **values)`가 Python 표준 `string.Template`으로 `${변수명}`을 치환한다.
JSON의 `{}`는 그대로 쓸 수 있고, 템플릿에서 달러 기호 자체는 `$$`로 쓴다.
삽입한 메모/원문/오류 메시지 안의 `$`나 JSON을 다시 템플릿으로 해석하지 않는다.
필수 변수가 없거나 파일을 찾지 못하면 오류를 내며 다른 프롬프트로 대체하지 않는다.
템플릿은 프로세스에서 캐시하므로 편집 후 서버/데모/실험 프로세스를 새로 시작한다.

| 파일 | 역할 및 입력 |
|---|---|
| poker_decision.txt | PokerLLM.decide의 게임 규칙·발언·메모 지시. `${action_selection}`을 삽입 |
| poker_action_selection.txt | 체크·콜·폴드·레이즈가 온전한 행동 선택임을 명시하는 현재 지시문 |
| poker_chat.txt | 인간에게 일반 대화로 답하는 chat 경로 |
| poker_mixed.txt | 혼합 의도의 인간 발언에 대한 추가 지시. `${player_id}`, `${speaker_id}` |
| poker_summary.txt | 공개 후 개인 메모 회고 |
| private_memories.txt | 모든 PlayerLLM 호출에 메모 첨부. `${system}`, `${long_memo}`, `${game_memo}`, `${round_memo}` |
| speech_interpreter.txt | 인간 발언의 game/chat/private_request 분해 |
| speech_retry.txt | 발언 해석 검증 실패 시 재시도 피드백. `${error}` |

판단 호출의 구성은 다음과 같다.

```python
system = render_prompt('poker_decision',
                       action_selection=render_prompt('poker_action_selection'))
```

혼합 응답이면 poker_mixed를 이어 붙이고, PlayerLLM이 private_memories로 세 메모를 첨부한다.
현재 판세·대화·검색 결과·feedback은 기존 JSON user 입력으로 전달한다.
모델별 통신은 llm_controller가 담당하므로 같은 템플릿이 Ollama/OpenAI에 적용된다.
엔진이 결정하는 행동 목록/스키마 제약과 유효성 검사는 기존 코드가 관리한다.
연습용 graph_practice/poker_llm_practice는 이 변경에 포함하지 않는다.

## 새 행동 지시문으로 실험 3 실행

```sh
.venv/bin/python -m experiments.run --stage 3 --cases 10
```

공급자와 모델은 기존 config를 따른다. 현재 config는 ollama/qwen3:8b다.
10은 상대별 사례 수다. B/C 각각 10사례 × control/both 2조건으로 총 40라운드이며,
재판단·검증 재시도·회고 때문에 LLM 호출은 40회보다 많을 수 있다.
새 지시문이 모든 조건에 적용되며 이전 지시문을 다시 실행하는 별도 비교군은 없다.

manifest.json의 source_hashes에 템플릿 `.txt`와 로더 코드 해시를 기록한다.
각 실제 system/user/schema와 응답은 기존 model_calls.jsonl에 보존된다.
실행 중 템플릿을 편집하지 않는다. 다음 실행은 새 run_id로 기존 결과와 분리된다.

옛 30사례 결과와 비교할 때 같은 B/C의 01~10 사례끼리 비교한다.
both 조건에는 앞선 상대 필터/공개 결과 첨부도 반영되므로 그 변화 전부를 프롬프트 하나의
효과라고 해석하지 않는다. control의 행동 변화와 force_bet 상황의 선택 분포를 함께 확인한다.
