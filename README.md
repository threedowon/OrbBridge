# OrbBridge

물리 오브 테이블 + 멜로디 screen을 연결하는 프로토타입입니다.  
하드웨어 없이 웹 table UI·WebSocket으로 전체 플로우를 테스트할 수 있습니다.

## 폴더 구조

```
OrbBridge/
├── bridge/
│   └── bridge.py          OSC(UDP) → WebSocket 중계 (독립 실행용)
│
├── server/                Python 서버 + 설정·데이터
│   ├── main.py            게임 로직, 시리얼/mock 입력, OSC 송신 (+ bridge 내장)
│   ├── bridge_service.py  OSC → WebSocket (main.py / exe에서 사용)
│   ├── mock_server.py     HTTP API + table/screen/admin 정적 파일 서빙
│   ├── launcher.py        PyInstaller 단일 exe 진입점
│   ├── register_tool.py   DS2431 오브 등록 GUI
│   ├── voice_register_tool.py  보이스 샘플 등록 GUI
│   ├── melody_register_tool.py 멜로디 JSON 편집 GUI (BPM · note · duration)
│   ├── config.json        실행 설정
│   ├── orb_mapping.json   오브 ID → voice 매핑
│   ├── stage_data.json    스테이지 정답 시퀀스
│   └── voice_catalog.json 보이스 timbre·샘플 카탈로그
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
├── admin/
│   └── index.html         관리 UI  →  /admin
│
├── firmware/              ESP master/slave (선택)
└── release/               Voice Orb Demo exe 빌드 (Windows / macOS)
```

## 역할 분담

| 구성요소 | 경로 | 역할 |
|---------|------|------|
| **table** | `table/` | 오브 올리기/내리기 (READY일 때만 동작) |
| **screen** | `screen/` | 멜로디 게임 — **start**로 게임 시작 |
| **admin** | `admin/` | 셀 상태·이벤트 로그·시리얼 상태 모니터 |
| **server** | `server/` | orb 이벤트 처리, OSC 출력, HTTP API |
| **bridge** | `bridge/` | OSC → WebSocket 중계 (**웹 UI 전용 어댑터**) |

### bridge가 왜 따로 있나?

- `main.py`는 게임 로직만 담당하고, UI로는 **OSC(UDP 9000)** 만 보냅니다.
- **브라우저**는 OSC를 직접 받기 어려워서, bridge가 OSC → **WebSocket(8765)** 로 바꿔 table/screen/admin에 푸시합니다.
- 나중에 screen이 **TouchDesigner·Unreal** 등으로 바뀌면 OSC를 직접 수신할 수 있으므로 **bridge는 필요 없습니다.** (그때 `main.py`에서 bridge 자동 시작만 끄면 됨)

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

웹 UI는 **양방향**입니다.

| 방향 | 프로토콜 | 용도 |
|------|----------|------|
| UI → server | HTTP (`POST /api/...`) | 버튼·드래그 등 사용자 입력 |
| server → UI | OSC → bridge → WebSocket | 실시간 상태 반영 |

## 사전 준비

Python 3 필요. Node.js·npm은 **사용하지 않습니다** (프론트는 순수 HTML/JS).

```bash
python3 -m pip install python-osc websockets pyserial
```

macOS에서는 `pip` 대신 `python3 -m pip` 또는 `pip3`를 사용하세요.

## 실행 (웹 개발 — mock 모드)

**터미널 하나**면 됩니다. `main.py`가 HTTP 서버와 bridge(WebSocket)를 함께 띄웁니다.

```bash
cd server
python3 main.py
```

브라우저:

| URL | 용도 |
|-----|------|
| http://127.0.0.1:8080/screen | 멜로디 게임 |
| http://127.0.0.1:8080/table | 오브 테이블 |
| http://127.0.0.1:8080/admin | 관리·디버그 |

웹 mock 테스트 시 `server/config.json`에서 `"input_mode": "table"` 로 설정하세요.

### `bridge/bridge.py`는 언제 쓰나?

독립 프로세스로 bridge만 따로 띄워보고 싶을 때의 **dev용 entry point**입니다.  
일상적인 웹 개발에서는 **`main.py`만 실행**하고, `bridge/bridge.py`는 **동시에 실행하지 마세요** — 포트 9000·8765가 충돌합니다.

| 실행 방식 | 명령 | 비고 |
|----------|------|------|
| **웹 개발 (권장)** | `python3 server/main.py` | bridge 내장 |
| bridge만 분리 테스트 | `python3 bridge/bridge.py` + (main에서 bridge 시작 제거 시) | 고급/레거시 |
| Windows 배포 | `release/build_release.bat` | `OrbBridge.zip` (Windows 전용) |
| macOS 배포 | `release/build_release_mac.sh` | `OrbBridge-mac.zip` (Mac에서 빌드) |

## Voice Orb Demo (배포용 exe / Mac 앱)

Python 없이 체험할 때 사용합니다. **OS별로 빌드·배포물이 다릅니다.**

| OS | 빌드 | 배포 zip | 실행 |
|----|------|----------|------|
| **Windows** | `release\build_release.bat` | `OrbBridge.zip` | `OrbBridge.exe` 더블클릭 |
| **macOS** | `release/build_release_mac.sh` | `OrbBridge-mac.zip` | `OrbBridge.command` 더블클릭 |

Windows에서 만든 `.exe`는 **Mac에서 실행되지 않습니다.** Mac 사용자에게는 Mac에서 빌드한 zip을 따로 전달해야 합니다.

### macOS: `library load disallowed by system policy` (PYI-18253)

다운로드한 앱에 macOS **격리(quarantine)** 속성이 붙으면, 서명되지 않은 Python 라이브러리 로드가 차단됩니다. 수신자 Mac에서:

```bash
cd "/path/to/OrbBridge"
xattr -cr .
chmod +x OrbBridge
./OrbBridge
```

또는 배포물의 **`OrbBridge.command`** 를 더블클릭하세요 (격리 해제 후 실행).

Mac 개발( Python 있음 ): 프로젝트 루트 `start.command` 더블클릭.


### 플레이 순서

1. `/screen`에서 **start** 클릭 → table에 `READY` 표시
2. `/table`에서 오브를 드래그해 그리드에 배치
3. `/screen` 악보에 오브 반영, 정답 판정·재생
4. 멜로디 완성 → table이 다시 대기 상태로 복귀

### 포트 충돌 (`Port already in use: OSC UDP 9000, WebSocket 8765`)

이전 `main.py` 또는 `bridge/bridge.py`가 아직 실행 중일 수 있습니다.

```bash
# macOS — 점유 프로세스 확인
lsof -i :9000 -i :8765

# 종료 (PID 확인 후)
kill <PID>
```

### 아이패드 / 다른 기기에서 table 열기

PC와 같은 Wi-Fi에 연결한 뒤:

1. `server/config.json` → `mock.host`를 `"0.0.0.0"`으로 변경
2. `server/bridge_service.py` → `WS_HOST = "0.0.0.0"`으로 변경  
   (독립 bridge 사용 시에는 `bridge/bridge.py`의 `WS_HOST`도 동일하게)
3. 방화벽에서 포트 **8080**, **8765** 허용
4. 아이패드 Safari: `http://<PC-IP>:8080/table`

## DS2431 오브 등록

새 오브 칩 UID를 `orb_mapping.json`에 등록할 때 사용합니다.

```bash
cd server
python3 register_tool.py
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

## Melody Register

`screen/melodies/*.json` 의 BPM, step별 `note`·`duration`·`lyric` 을 편집합니다.  
저장 시 **stage_data.json** 정답 시퀀스를 자동 동기화할 수 있습니다 (오브 정답 판정용).

```bash
# Windows
melody_register.bat

# 또는
cd server
python melody_register_tool.py
python melody_register_tool.py --list
```

| 기능 | 설명 |
|------|------|
| 멜로디 목록 | `biplane`, `love_dive`, `custom` 등 |
| BPM | 40–240, 1박 길이(ms)·총 재생 시간 표시 |
| Steps | note (C4–C5 또는 `?`), duration(박), lyric |
| 미리듣기 | step 하이라이트 + 비프음 (Windows) |
| 저장 | JSON 저장 + 선택 시 `stage_data.json` 동기화 |

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
| `mock.host` / `mock.port` | table·screen·admin HTTP 서버 |
| `serial.port` | ESP / DS2431 리더 COM 포트 (macOS: `/dev/tty.usbserial-...`) |
| `osc.host` / `osc.port` | OSC 송신 대상 (bridge 또는 TD/Unreal 수신 포트) |

### 데이터 파일

| 파일 | 설명 |
|------|------|
| `orb_mapping.json` | 오브 UID → `{ "voice": "aspa_1" }` |
| `stage_data.json` | 스테이지별 정답 음표 시퀀스 |
| `voice_catalog.json` | 보이스 timbre·샘플 경로 |
| `sounds/` | 보이스 샘플 mp3/wav (없어도 Web Audio 톤으로 동작) |

## 포트 정리

| 포트 | 용도 |
|------|------|
| **8080** | HTTP — table / screen / admin / API |
| **8765** | WebSocket — UI 실시간 연동 (웹 전용) |
| **9000** | OSC (UDP) — server → bridge (또는 TD/Unreal) |

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
