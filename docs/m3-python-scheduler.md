# M3 Python Scheduler

M3는 Listen·Play·DFesta의 박자 계산과 MIDI 출력을 브라우저에서 Python으로
옮긴다. 브라우저는 Python이 보낸 현재 beat를 악보에 표시할 뿐 다음 실행
시각을 계산하지 않는다.

## 실행 경로

```text
Serial / HTTP game command
  → main.py
  → PlaybackScheduler
      ├─ absolute perf_counter deadline
      ├─ midi_out.trigger_many()
      └─ queue → 별도 publisher → WebSocket playback event
            → screen beat 표시
```

## 모드

- Listen: 준비 지연 없이 시작하며 `_listen` MIDI route로 정답 멜로디를 낸다.
- Play: 250ms 뒤 시작하며 현재 각 column에 놓인 오브의 voice·pitch를 낸다.
- DFesta: 250ms 뒤 시작하며 현재 오브의 고유 voice 전체로 정답 멜로디를
  합창한다.

모든 offset은 BPM과 앞선 step duration의 누적값으로 한 번 계산한다.
각 beat가 끝난 뒤 다음 beat를 상대 지연하는 방식이 아니므로 처리 시간이
누적되어 템포가 밀리지 않는다.

Windows의 낮은 해상도 monotonic clock 및 화면 callback과의 결합을 제거한
후속 수정은 [MIDI 타이밍 조사](midi-timing.md)를 참조한다. 절대 deadline은
누적 오차를 방지하지만 OS나 driver가 스레드를 정지시킬 때의 실시간 보장은 아니다.

## 동시 실행과 취소

- 새 Listen·Play·DFesta 요청은 기존 playback을 취소하고 새 run을 만든다.
- `RUNNING`을 벗어나는 `RESETTING` 전이와 `READY` 복귀는 실행 중 playback을 취소한다.
- MIDI 오류가 나도 Scheduler thread와 beat 표시 이벤트는 계속 진행한다.
- 종료 시 `PlaybackScheduler.close()`로 worker를 정리한다.

## WebSocket 이벤트

- `/game/playback/start`: `run_id`, `mode`, `step_count`
- `/game/playback/beat`: `run_id`, `mode`, `step`, `duration_ms`
- `/game/playback/end`: `run_id`, `mode`, `cancelled`

`run_id`가 이전 실행보다 작으면 screen이 늦게 도착한 이벤트로 보고 무시한다.
음향은 Python MIDI 출력 하나만 사용하며 Screen은 beat 위치만 표시한다.

## 테스트 범위

- melody BPM·note·REST·duration 검증
- Listen 즉시 시작과 `_listen` route
- Play의 column별 voice·physical pitch
- DFesta voice 중복 제거와 정답 pitch
- 250ms 준비 시간과 duration 누적 offset
- start·beat·MIDI·end 이벤트 순서
- 새 실행의 이전 실행 취소
- close 멱등성과 종료 후 start 거부
