# M3 Python Scheduler

M3는 Listen·Play·Dispensa의 박자 계산과 MIDI 출력을 브라우저에서 Python으로
옮긴다. 브라우저는 Python이 보낸 현재 beat를 악보에 표시할 뿐 다음 실행
시각을 계산하지 않는다.

## 실행 경로

```text
Serial / HTTP game command
  → main.py
  → PlaybackScheduler
      ├─ absolute monotonic deadline
      ├─ midi_out.trigger_many()
      └─ WebSocket playback event
            → screen beat 표시
```

## 모드

- Listen: 준비 지연 없이 시작하며 `_listen` MIDI route로 정답 멜로디를 낸다.
- Play: 250ms 뒤 시작하며 현재 각 column에 놓인 오브의 voice·pitch를 낸다.
- Dispensa: 250ms 뒤 시작하며 현재 오브의 고유 voice 전체로 정답 멜로디를
  합창한다.

모든 offset은 BPM과 앞선 step duration의 누적값으로 한 번 계산한다.
각 beat가 끝난 뒤 다음 beat를 상대 지연하는 방식이 아니므로 처리 시간이
누적되어 템포가 밀리지 않는다.

## 동시 실행과 취소

- 새 Listen·Play·Dispensa 요청은 기존 playback을 취소하고 새 run을 만든다.
- FINISHED·RESETTING·READY 전이는 실행 중 playback을 취소한다.
- MIDI 오류가 나도 Scheduler thread와 beat 표시 이벤트는 계속 진행한다.
- 종료 시 `PlaybackScheduler.close()`로 worker를 정리한다.

## WebSocket 이벤트

- `/game/playback/start`: `run_id`, `mode`, `step_count`
- `/game/playback/beat`: `run_id`, `mode`, `step`, `duration_ms`, `notes JSON`
- `/game/playback/end`: `run_id`, `mode`, `cancelled`

`run_id`가 이전 실행보다 작으면 screen이 늦게 도착한 이벤트로 보고 무시한다.
`notes JSON`은 `audio_output: web` 개발 fallback에서만 즉시 재생하며, MIDI
모드에서는 시각화 정보로만 사용한다.

## 테스트 범위

- melody BPM·note·REST·duration 검증
- Listen 즉시 시작과 `_listen` route
- Play의 column별 voice·physical pitch
- Dispensa voice 중복 제거와 정답 pitch
- 250ms 준비 시간과 duration 누적 offset
- start·beat·MIDI·end 이벤트 순서
- 새 실행의 이전 실행 취소
- close 멱등성과 종료 후 start 거부
