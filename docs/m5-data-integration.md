# M5 데이터 통합

M5는 분리돼 있던 멜로디 표시·재생·정답 데이터를 하나의 JSON으로 통합한다.

## 단일 원본

```text
data/config.json
  └─ melody_manager 순서 선택
       └─ data/melodies/{id}.json
            ├─ GameEngine 정답 판정
            ├─ Python Scheduler 재생
            ├─ screen 악보 렌더링
            └─ table 정답 표시
```

- 모든 JSON은 `data/` 아래에만 둔다.
- `stage_data.json`은 제거한다.
- 멜로디 `steps`가 정답 순서와 재생 순서의 공통 원본이다.
- `game_rules`가 TimeOver·Idle·DFesta 입력 횟수를 함께 보관한다.
- `current_stage`는 기존 운영 표시값으로 `data/config.json`에 유지한다.
- 여러 멜로디는 `melody_rotation.playlist` 순서로 게임마다 순환한다.
- 로드·규칙 검증·순번 관리는 `server/game/melody_manager.py` 하나가 담당한다.

## 런타임 갱신

서버는 게임 시작 직전에 `data/config.json`과 다음 멜로디를 다시 읽는다.
같은 메모리 snapshot에서 정답 시퀀스와 Scheduler 입력을 만들고,
`/game/melody`를 `/game/state`보다 먼저 전송한다. 따라서 Melody Register 저장 후
다음 Admin start 한 번으로 Screen·Admin과 Python 판정·재생이 함께 갱신된다.
성공한 start에서만 순번이 이동하므로 거부되거나 실패한 start는 곡을 건너뛰지
않는다.

연결 직후 WebSocket snapshot에도 전체 멜로디를 포함하며, HTTP 복구용
`GET /api/melody`도 같은 메모리 snapshot을 반환한다.

## 멜로디 형식

```json
{
  "songId": "dynamite",
  "bpm": 114,
  "range": ["C4", "C5"],
  "game_rules": {
    "time_over_s": 120,
    "idle_s": 30,
    "dfesta_presses_required": 1
  },
  "steps": [
    {"step": 1, "note": "C4", "duration": 0.5},
    {"step": 2, "note": "REST", "duration": 1.0}
  ]
}
```

## 완료 기준

- 멜로디 파일 하나를 바꾸면 판정·화면·재생이 같은 start에서 갱신된다.
- `stage_data.json`, 편집기 동기화 옵션, `/screen/melodies` 경로가 남지 않는다.
- WebSocket 재접속 시 게임 상태와 같은 멜로디를 복구한다.
- PyInstaller 배포물도 `data/` 구조를 그대로 사용한다.

