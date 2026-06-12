# OrbBridge Web UI Prototype

OrbBridge + 가짜 테이블 + 멜로디 게임 screen으로 하드웨어 없이 전체 플로우를 테스트합니다.

## 역할 분담

| 구성요소 | 역할 |
|---------|------|
| **table** (`/table`) | 오브 올리기/내리기만 (ready일 때만 동작) |
| **screen** (`/screen`) | UXFlow 멜로디 게임 — **시작** 버튼으로 게임 시작 |
| **OrbBridge** | orb 이벤트 처리, OSC 출력, ready 신호 |
| **bridge.py** | OSC → WebSocket 중계 |

## 데이터 흐름

```
screen [시작] ──POST /api/game/start──► OrbBridge
                                           ├─ OSC /game/ready [1] ──► bridge ──► table (READY)
                                           └─ OSC /game/start

table [셀 클릭] ──POST /api/event──► OrbBridge handle_event()
                                           └─ OSC /orb/added ──► bridge ──► screen (악보 반영)

screen [멜로디 완성] ──POST /api/game/end──► OrbBridge
                                           └─ OSC /game/ready [0] ──► table (대기)
```

## 실행 순서

```bash
pip install python-osc websockets pyserial

# 1. 브릿지
python bridge/bridge.py

# 2. OrbBridge (mock mode)
cd OrbBridge
python main.py

# 3. 브라우저
# http://127.0.0.1:8080/screen  — 멜로디 게임 (시작 버튼)
# http://127.0.0.1:8080/table   — 오브 시뮬레이터
```

1. `/screen`에서 **▶ 시작** 클릭 → table에 `READY` 표시
2. `/table`에서 orb 선택 후 셀 클릭
3. `/screen` 악보에 오브 반영, 박자에 맞춰 정답 판정
4. 멜로디 완성 → table `대기 중`으로 복귀

## 설정 (`OrbBridge/config.json`)

```json
{
  "input_mode": "mock",
  "melody": "biplane"
}
```

| `input_mode` | 설명 |
|--------------|------|
| `mock` | 가짜 테이블 HTTP |
| `serial` | 실제 ESP32 USB |

## OSC 주소

| 주소 | 설명 |
|------|------|
| `/game/start` | 게임 시작 (screen에서) |
| `/game/ready` | `1`=테이블 ready, `0`=대기 |
| `/game/end` | 게임 종료 |
| `/game/listen` | 목표 멜로디 다시 듣기 (루프 일시정지) |
| `/game/play` | 비트 진행 시작 — 올려둔 orb 소리 재생 |
| `/orb/added` | 오브 올림 |
| `/orb/removed` | 오브 제거 |
