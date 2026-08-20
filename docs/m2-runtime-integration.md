# M2 Python 런타임 연결

M2는 M1의 순수 `GameEngine`을 실제 `main.py` 실행 경로에 연결한다.

## 입력 경로

```text
Serial ─┐
HTTP ───┴─→ GameRuntime Queue → GameEngine
                                  ├─ cached snapshot → HTTP UI
                                  ├─ state event → WebSocket
                                  └─ orb added → midi_out 단음
```

- Serial과 HTTP 스레드는 `GameEngine`을 직접 수정하지 않는다.
- 모든 `start`, `end`, `reset`, `added`, `removed`, 활동, `tick`은
  `GameRuntime` worker 한 곳에서 순서대로 처리한다.
- 타이머는 worker가 50ms 간격으로 `tick()`하여 화면과 무관하게 처리한다.
- 오브 메시지를 먼저 보낸 뒤 상태를 알리도록 명령 전이는 호출자가 발행한다.

## 상태와 reset

- 시작은 `READY → RUNNING`으로 직접 전이한다.
- 정답 후 DFesta 기준 충족, TimeOver, Idle, 수동 `end`, `reset`은 모두
  `RUNNING → RESETTING`으로 직접 전이한다.
- 오브가 남아 있으면 `RESETTING`에서 전부 제거될 때까지 기다린다.
- 웹 table은 `RESETTING` 동안 새 배치와 이동은 막고 제거만 허용한다.

## snapshot API

- `GET /api/game/state`: 상태, 종료 사유, 셀, 정답, 경과·잔여 시간
- `GET /api/status`: 시스템 상태와 `game` snapshot
- `GET /api/cells`: `GameEngine` snapshot을 admin/table 형식으로 변환

게임 실행 여부도 별도 boolean 없이 `state == RUNNING`으로 판단한다.

## M2 테스트 범위

- worker 시작·종료와 명령 오류 전달
- 여러 producer의 변경이 단일 worker 스레드에서 실행되는지 검증
- `start`, `end`, `reset`, `added`, `removed`, 활동 연결
- 백그라운드 tick의 TimeOver·Idle과 직접 `RESETTING` 전이
- snapshot 복사·barrier·engine 재구성 검증
- M1 상태 모델 전체 회귀 테스트

## 이후 단계

- Listen·Play·DFesta Python Scheduler 이관: M3
- Python 직접 WebSocket 전송과 재접속 snapshot: M4
- melody 데이터 단일 원본 통합: M5 완료
