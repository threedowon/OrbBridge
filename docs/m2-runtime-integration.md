# M2 Python 런타임 연결

M2는 M1의 순수 `GameEngine`을 실제 `main.py` 실행 경로에 연결한다.

## 입력 경로

```text
Serial ─┐
HTTP ───┴─→ GameRuntime Queue → GameEngine
                                  ├─ cached snapshot → HTTP UI
                                  ├─ phase event → WebSocket
                                  └─ orb added → midi_out 단음
```

- Serial과 HTTP 스레드는 `GameEngine`을 직접 수정하지 않는다.
- 모든 start, end, reset, added, removed, activity, tick은 `GameRuntime`
  worker 한 곳에서 순서대로 처리한다.
- timeout은 worker가 50ms 간격으로 `tick()`하여 화면과 무관하게 처리한다.
- 관련 오브 메시지를 먼저 보낸 뒤 phase를 알리도록 command transition은
  호출자가 명시적으로 publish한다.

## 상태와 reset

- 기본 시작은 `READY → STARTING → RUNNING`이다.
- `start_auto_complete`가 없거나 `true`이면 STARTING을 즉시 완료한다.
- 시작 연출이 필요하면 `start_auto_complete: false`로 두고
  `POST /api/game/start-complete`를 호출한다.
- 정답 완료·total timeout·idle timeout은 `FINISHED`를 알린 직후 자동으로
  reset을 요청한다.
- 오브가 남아 있으면 `RESETTING`에서 전부 제거될 때까지 기다린다.
- 수동 `end`는 `FINISHED`를 유지하며 `reset` 입력을 기다린다.
- 웹 table은 RESETTING 동안 새 배치와 이동은 막고 제거만 허용한다.

## snapshot API

- `GET /api/game/state`: phase, finish reason, cells, answers, elapsed/remaining time
- `GET /api/status`: 시스템 상태와 `game` snapshot
- `GET /api/cells`: `GameEngine` snapshot을 admin/table 형식으로 변환

`game_active`는 호환성을 위해 유지하며 `phase == RUNNING`일 때만 `true`다.

## M2 테스트 범위

- worker 시작·종료와 command 오류 전달
- 여러 producer의 mutation이 단일 worker thread에서 실행되는지 검증
- start, complete, finish, reset, added, removed, activity 연결
- background tick의 total/idle timeout과 자동 RESETTING 전이
- snapshot 복사·barrier·engine 재구성 검증
- 기존 M1 상태 모델 전체 회귀 테스트

## 이후 단계

- Listen·Play·Dispensa Python Scheduler 이관: M3
- Python 직접 WebSocket broadcast와 재접속 snapshot: M4
- melody 데이터 단일 원본 통합: M5
