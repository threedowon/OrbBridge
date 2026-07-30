# M1 게임 상태 규칙

M1은 외부 입출력과 분리된 순수 Python 상태 모델이다.

## 상태 전이

```text
READY
  └─ START
       ↓
STARTING
  └─ START_COMPLETE
       ↓
RUNNING
  ├─ 정답 완성 후 설정된 DFesta 입력 횟수 충족
  ├─ 전체 제한시간 초과
  ├─ 무입력 제한시간 초과
  └─ 수동 종료
       ↓
FINISHED
  └─ RESET
       ↓
RESETTING
  └─ 테이블의 모든 오브 제거
       ↓
READY
```

- `STARTING`은 조명·인트로 등 시작 연출을 실행할 구간이다.
- 게임 타이머와 정답 판정은 `START_COMPLETE`로 `RUNNING`에 진입할 때 시작한다.
- 시작 연출이 없다면 런타임에서 `START` 직후 `START_COMPLETE`를 보낸다.
- 자동 완료·timeover·idle timeout은 `FINISHED`를 알린 뒤 `RESETTING`으로 간다.
- 수동 `end`는 `FINISHED`를 유지하고 별도 `reset`을 기다린다.
- 테이블이 비어 있으면 `RESETTING`을 한 runtime tick 동안 알린 뒤 `READY`가 된다.

## 입력과 완료

- 실제 점유 상태를 잃지 않도록 added/removed는 모든 상태에서 기록한다.
- 정답·오답 판정, 완료 검사, 무입력 타이머 갱신은 `RUNNING`에서만 한다.
- 한 오브가 다른 셀에 added되면 이전 셀에서 이동한 것으로 처리한다.
- 이미 처리된 동일 added와 오래된 removed는 상태를 바꾸지 않는다.
- `R` 또는 `REST` 위치는 비어 있어야 한다. 오브가 있으면 오답이다.
- 정답 위치가 모두 채워져도 오답·REST·미등록 오브가 하나라도 있으면 완료되지 않는다.
- 정답 완성만으로 종료되지 않고 `dispensa_presses_required`만큼 DFesta를 눌러야 한다.
- 미등록 오브(`known=False`)는 정답 셀에 있어도 완료 조건을 충족하지 않는다.
- `START` 시점에는 테이블이 비어 있어야 한다.

## 타이머

- 전체 제한시간은 `RUNNING` 진입 시점부터 계산한다.
- 무입력 제한시간도 `RUNNING` 진입 시 시작한다.
- 실제 점유 상태를 바꾼 added/removed와 Listen·Play·DFesta 입력이 idle 시간을 갱신한다.
- 중복 added, 빈 셀 removed, 오래된 removed는 idle 시간을 갱신하지 않는다.
- 이벤트가 제한시간과 정확히 같은 시각에 도착하면 timeout을 먼저 적용한다.
- 두 timeout의 마감이 같으면 전체 제한시간을 우선한다.

## M1 완료 범위

- 구현: `server/game_engine.py`
- 자동 테스트: `tests/test_game_engine.py`
- 런타임 연결은 M2에서 진행한다.
