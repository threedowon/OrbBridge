# OrbBridge

물리 오브 테이블 + 멜로디 screen을 연결하는 프로토타입입니다.  
하드웨어 없이 웹 table UI·WebSocket으로 전체 플로우를 테스트할 수 있습니다.

## 폴더 구조

```
OrbBridge/
├── bridge/
│   └── bridge.py          OSC(UDP) → WebSocket 중계
│
├── server/                Python 서버 + 설정·데이터
│   ├── main.py            게임 로직, 시리얼/mock 입력, OSC 송신
│   ├── mock_server.py     HTTP API + table/screen 정적 파일 서빙
│   ├── register_tool.py   DS2431 오브 등록 GUI 실행
│   ├── register_gui.py    등록 GUI (tkinter)
│   ├── register_core.py   시리얼 수신·등록 로직
│   ├── mapping_store.py   orb_mapping.json 읽기/쓰기
│   ├── config.json        실행 설정
│   ├── orb_mapping.json   오브 ID → voice 매핑
│   └── stage_data.json    스테이지 정답 시퀀스
│
├── table/
│   └── index.html         테이블 UI  →  /table
│
├── screen/
│   ├── index.html         멜로디 UI  →  /screen
│   ├── js/                게임 엔진, 악보, 오디오
│   ├── css/
│   └── melodies/          멜로디 JSON
│
├── firmware/              ESP master/slave (선택)
└── release/               Voice Orb Demo exe 빌드 스크립트
```

## 역할 분담

| 구성요소 | 경로 | 역할 |
|---------|------|------|
| **table** | `table/` | 오브 올리기/내리기 (READY일 때만 동작) |
| **screen** | `screen/` | 멜로디 게임 — **start**로 게임 시작 |
| **server** | `server/` | orb 이벤트 처리, OSC 출력, mock HTTP |
| **bridge** | `bridge/` | OSC → WebSocket 중계 (UI 실시간 연동) |

## 데이터 흐름

```
screen [start]
    └─ POST /api/game/start ──► server (main.py)
                                    ├─ OSC /game/ready [1] ──► bridge ──► table (READY)
                                    └─ OSC /game/start

table [오브 배치]
    └─ POST /api/event ──► server
                              └─ OSC /orb/added ──► bridge ──► screen (악보 반영)

table [다시듣기 / 재생]
    └─ POST /api/game/listen|play ──► server
                                          └─ OSC /game/listen|play ──► screen

screen [멜로디 완성]
    └─ POST /api/game/end ──► server
                                  └─ OSC /game/ready [0] ──► table (대기)
```

## 사전 준비

```bash
pip install python-osc websockets pyserial
```

## 실행 (mock 모드)

터미널 3개 (또는 백그라운드)에서 순서대로 실행합니다.

```bash
# 1. OSC → WebSocket 브릿지
python bridge/bridge.py

# 2. 게임 서버 (HTTP + mock)
cd server
python main.py

# 3. 브라우저
# http://127.0.0.1:8080/screen   — 멜로디 게임
# http://127.0.0.1:8080/table    — 오브 테이블
```

### 플레이 순서

1. `/screen`에서 **start** 클릭 → table에 `READY` 표시
2. `/table`에서 오브를 드래그해 그리드에 배치
3. `/screen` 악보에 오브 반영, 정답 판정·재생
4. 멜로디 완성 → table이 다시 대기 상태로 복귀

### 아이패드 / 다른 기기에서 table 열기

PC와 같은 Wi-Fi에 연결한 뒤:

1. `server/config.json` → `mock.host`를 `"0.0.0.0"`으로 변경
2. `bridge/bridge.py` → `WS_HOST = "0.0.0.0"`으로 변경
3. Windows 방화벽에서 포트 **8080**, **8765** 허용
4. 아이패드 Safari: `http://<PC-IP>:8080/table`

## DS2431 오브 등록

새 오브 칩 UID를 `orb_mapping.json`에 등록할 때 사용합니다.

```bash
cd server
python register_tool.py
```

- GUI에서 **COM 포트 선택** → 오브 태그
- **등록됨** ID → 기존 voice 표시
- **신규** ID → voice 입력 (`aspa_1` 형식) 후 등록

ESP → PC 시리얼 (한 줄 JSON):

```json
{"type":"ds2431","id":"2D:E8:B1:9F:24:00:00:AC"}
```

| 필드 | 설명 |
|------|------|
| `type` | `ds2431`, `ds2431_detected`, `detect`, `orb_detect` |
| `id` | UID (`2DE8B1...` 또는 `2D:E8:...` 형식 모두 가능) |

voice 이름 규칙: `그룹_번호` (예: `aspa_1`, `ive_2`, `cortis_1`)  
같은 그룹(`aspa_1`, `aspa_2`)은 table UI에서 **같은 색**으로 표시됩니다.

## 설정 (`server/config.json`)

```json
{
  "current_stage": 1,
  "melody": "biplane",
  "input_mode": "table",
  "mock": {
    "host": "127.0.0.1",
    "port": 8080
  },
  "serial": {
    "port": "COM13",
    "baudrate": 115200
  },
  "osc": {
    "host": "127.0.0.1",
    "port": 9000
  }
}
```

| 항목 | 설명 |
|------|------|
| `input_mode` | `table` — 웹 `/table` UI 입력 / `serial` — ESP32 USB 시리얼 |
| `melody` | `screen/melodies/` 안 멜로디 ID (`biplane` 등) |
| `mock.host` / `mock.port` | table·screen HTTP 서버 |
| `serial.port` | ESP / DS2431 리더 COM 포트 |
| `osc.host` / `osc.port` | OSC 수신 (bridge.py와 동일 포트) |

### 데이터 파일

| 파일 | 설명 |
|------|------|
| `orb_mapping.json` | 오브 UID → `{ "voice": "aspa_1" }` |
| `stage_data.json` | 스테이지별 정답 음표 시퀀스 |

## 포트 정리

| 포트 | 용도 |
|------|------|
| **8080** | HTTP — table / screen / API |
| **8765** | WebSocket — UI 실시간 연동 |
| **9000** | OSC (UDP) — server → bridge |

## OSC 주소

| 주소 | 설명 |
|------|------|
| `/game/start` | 게임 시작 |
| `/game/ready` | `1` = 테이블 READY, `0` = 대기 |
| `/game/end` | 게임 종료 |
| `/game/listen` | 목표 멜로디 다시 듣기 |
| `/game/play` | 배치된 orb 소리 재생 |
| `/orb/added` | 오브 올림 |
| `/orb/removed` | 오브 제거 |

## HTTP API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/api/event` | table → 오브 added/removed |
| POST | `/api/game/start` | screen → 게임 시작 |
| POST | `/api/game/end` | 게임 종료 |
| POST | `/api/game/listen` | 다시 듣기 |
| POST | `/api/game/play` | 재생 |
| GET | `/api/orbs` | 등록된 오브 목록 |
