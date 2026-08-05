# M1 게임 상태 규칙

M1은 외부 입출력과 분리된 순수 Python 상태 모델이다.

## 상태와 전이

상태는 다이어그램과 동일한 세 가지만 사용한다.

```text
READY
  └─ start와 CheckStart 충족
       ↓
RUNNING
  ├─ IsCorrect 이후 DFestaPress 기준 횟수 충족
  ├─ IsIdle
  ├─ IsTimeOver
  ├─ end
  └─ reset
       ↓
RESETTING
  └─ TableIsClear
       ↓
READY
```

- `start`는 `READY → RUNNING`으로 직접 전이하고 두 타이머를 시작한다.
- 모든 종료 조건과 `end`, `reset`은 `RUNNING → RESETTING`으로 직접 전이한다.
- 종료 사유는 `complete`, `idle`, `time_over`, `manual` 중 하나다.
- 테이블이 이미 비어 있어도 `RESETTING`을 한 runtime tick 동안 노출한다.
- 오브가 남아 있으면 모두 제거될 때까지 `RESETTING`을 유지한다.

## 입력과 완료

- 실제 점유 상태를 잃지 않도록 `added`와 `removed`는 모든 상태에서 기록한다.
- 정답·오답 판정과 타이머 갱신은 `RUNNING`에서만 한다.
- 한 오브가 다른 셀에 추가되면 이전 셀에서 이동한 것으로 처리한다.
- 이미 처리된 동일 `added`와 오래된 `removed`는 상태를 바꾸지 않는다.
- `R` 또는 `REST` 위치는 비어 있어야 한다. 오브가 있으면 오답이다.
- 정답 위치가 모두 채워져도 오답·REST·미등록 오브가 있으면 완료되지 않는다.
- 정답만으로 종료하지 않고 `dfesta_presses_required`만큼 DFesta를 눌러야 한다.
- 미등록 오브(`known=False`)는 정답 셀에 있어도 완료 조건을 충족하지 않는다.
- `start` 시점에는 테이블이 비어 있어야 한다.

## 타이머

- TimeOver와 Idle 타이머는 `RUNNING` 진입 시점부터 계산한다.
- 실제 점유를 바꾼 `added`·`removed`와 `listen`·`play`·`dfesta`가 Idle을 초기화한다.
- 중복 `added`, 빈 셀 `removed`, 오래된 `removed`는 Idle을 초기화하지 않는다.
- 이벤트가 제한 시각과 정확히 같은 시각에 도착하면 타이머 종료를 먼저 적용한다.
- 두 타이머의 마감이 같으면 TimeOver를 우선한다.

## M1 완료 범위

- 구현: `app/game_engine.py`
- 자동 테스트: `tests/test_game_engine.py`
- 런타임 연결: M2
