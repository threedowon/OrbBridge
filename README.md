# OrbBridge

물리 오브 테이블 + 멜로디 screen을 연결하는 프로토타입입니다.  
하드웨어 없이 웹 table UI·WebSocket으로 전체 플로우를 테스트할 수 있습니다.

## 폴더 구조

```
OrbBridge/
├── server/                Python 서버 + 설정·데이터
│   ├── main.py            Serial/HTTP 어댑터, WebSocket·MIDI 출력
│   ├── game_engine.py     상태·판정·완료·timeout 순수 로직
│   ├── game_runtime.py    입력 Queue·단일 writer·tick·snapshot
│   ├── scheduler.py       Listen·Play·DFesta 절대시각 Scheduler
│   ├── websocket_service.py Python → 브라우저 직접 전송·재접속 snapshot
│   ├── mock_server.py     HTTP API + table/screen/admin 정적 파일 서빙
│   ├── launcher.py        PyInstaller 단일 exe 진입점
│   ├── register_tool.py   DS2431 오브 등록 GUI
│   ├── voice_register_tool.py  보이스 샘플 등록 GUI
│   ├── melody_register_tool.py 멜로디 JSON 편집 GUI (전체 관리)
│   ├── melody_editor_tool.py   Melody Editor — BPM·note·duration 간편 편집
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
| **table** | `table/` | RUNNING 중 배치·제거, RESETTING 중 제거 |
| **screen** | `screen/` | 멜로디 게임 — **start**로 게임 시작 |
| **admin** | `admin/` | 셀 상태·이벤트 로그·시리얼 상태 모니터 |
| **server** | `server/` | 게임 상태의 기준, orb 이벤트 처리, WebSocket·MIDI 출력, HTTP API |

### 웹 인터페이스 동기화

- Python이 `screen`, `table`, `admin`에 **WebSocket(8765)** 으로 직접 전송합니다.
- 연결 직후 전체 snapshot을 먼저 보내므로 새로고침·재접속 뒤에도 상태를 복구합니다.
- 게임 상태는 `/game/state` 이벤트 하나만 기준으로 사용합니다.

### 게임 용어

| 구분 | 표준 값 |
|------|---------|
| 게임 상태 | `READY`, `RUNNING`, `RESETTING` |
| 게임 제어 이벤트 | `start`, `end`, `reset` |
| 재생·버튼 이벤트 | `listen`, `play`, `dfesta` |
| 종료 사유 | `complete`, `idle`, `time_over`, `manual` |
| 테이블 하트비트 점유값 | `occupancy_mask` |

게임 상태는 항상 `state` 필드에만 담습니다. `start`는 바로 `RUNNING`으로
전이하고, 모든 종료 조건과 `end`·`reset`은 바로 `RESETTING`으로 전이합니다.

## 데이터 흐름

```
screen [start]
    └─ POST /api/game/start ──► server (main.py)
                                    └─ WebSocket /game/state ──► 모든 UI

table [오브 배치]
    └─ POST /api/event ──► server
                              └─ GameRuntime Queue → GameEngine
                                   ├─ MIDI 단음
                                   └─ WebSocket /orb/added ──► 모든 UI

table [다시듣기 / 재생 / 디페스타]
    └─ POST /api/game/listen|play|dfesta ──► server
          └─ Python Scheduler ─┬─► MIDI note batch
                               └─► WebSocket playback beat ──► screen 표시

GameEngine [DFesta 완료·Idle·TimeOver·end·reset]
    └─ RUNNING → RESETTING ──► WebSocket /game/state ──► 모든 UI

RESETTING
    └─ 오브를 모두 제거하면 READY
```

웹 UI는 **양방향**입니다.

| 방향 | 프로토콜 | 용도 |
|------|----------|------|
| UI → server | HTTP (`POST /api/...`) | 버튼·드래그 등 사용자 입력 |
| server → UI | WebSocket | 실시간 상태·재생 beat 반영 |

## 사전 준비

Python 3 필요. Node.js·npm은 **사용하지 않습니다** (프론트는 순수 HTML/JS).

```bash
python3 -m pip install websockets pyserial
```

macOS에서는 `pip` 대신 `python3 -m pip` 또는 `pip3`를 사용하세요.

## 실행 (웹 개발 — mock 모드)

**터미널 하나**면 됩니다. `main.py`가 HTTP와 WebSocket 서버를 함께 띄웁니다.

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

| 실행 방식 | 명령 | 비고 |
|----------|------|------|
| **웹 개발 (권장)** | `python3 server/main.py` | HTTP + WebSocket |
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

1. `/screen`에서 **start** 클릭 → Python 상태가 `RUNNING`으로 전환
2. `/table`에서 오브를 드래그해 그리드에 배치
3. Python `GameEngine`이 정답·timeout 판정, `/screen`은 악보와 재생 표시
4. 정답 완성 후 DFesta 버튼 → `RESETTING` → 오브를 모두 제거하면 `READY`

### 포트 충돌 (`WebSocket 8765 already in use`)

이전 `main.py`가 아직 실행 중일 수 있습니다.

```bash
# macOS — 점유 프로세스 확인
lsof -i :8765

# 종료 (PID 확인 후)
kill <PID>
```

### 아이패드 / 다른 기기에서 table 열기

PC와 같은 Wi-Fi에 연결한 뒤:

1. `server/config.json` → `mock.host`를 `"0.0.0.0"`으로 변경
2. 필요하면 `config.json`에 `"websocket": {"host": "0.0.0.0"}` 추가
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

## Melody Editor

지금 쓰는 멜로디(`screen/melodies/config.json` → `active`, 기본 `biplane`)의 **BPM · note · duration** 만 빠르게 조절하는 간단 창입니다.  
가사·제목·다른 멜로디 관리는 **Melody Register**를 사용하세요.

```bash
melody_editor.bat
# 또는
cd server && python melody_editor_tool.py
```

저장 시 `stage_data.json` 은 자동 동기화됩니다.

**OrbBridge.exe 배포판:** `release/build_release.bat` 빌드 시 `MelodyEditor.exe`가 함께 포함됩니다.  
두 exe는 같은 폴더의 `melodies/`·`stage_data.json`을 사용합니다 — Melody Editor로 저장 → Screen 새로고침 → start 시 정답 반영.

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
  }
}
```

| 항목 | 설명 |
|------|------|
| `input_mode` | `table` — 웹 `/table` UI 입력 / `serial` — ESP32 USB 시리얼 |
| `melody` | `screen/melodies/` 안 멜로디 ID (`biplane` 등) |
| `mock.host` / `mock.port` | table·screen·admin HTTP 서버 |
| `serial.port` | ESP / DS2431 리더 COM 포트 (macOS: `/dev/tty.usbserial-...`) |

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

## WebSocket 이벤트

| 주소 | 설명 |
|------|------|
| `/game/state` | 실제 상태·종료 이유·revision·남은 시간·이전 상태 |
| `/game/playback/start` | Listen·Play·DFesta 재생 시작 |
| `/game/playback/beat` | 현재 beat와 브라우저 오디오 fallback 정보 |
| `/game/playback/end` | 재생 종료 |
| `/orb/added` | 오브 올림 |
| `/orb/removed` | 오브 제거 |
| `/orb/recovered` | 유실된 added 자가복구 추정 — **admin 전용(미상)**, screen 은 무시 |

## HTTP API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/api/event` | table → 오브 added/removed |
| POST | `/api/game/start` | screen → 게임 시작 |
| POST | `/api/game/end` | 게임 종료 |
| POST | `/api/game/reset` | 리셋·오브 제거 대기 |
| POST | `/api/game/listen` | 다시 듣기 |
| POST | `/api/game/play` | 재생 |
| POST | `/api/game/dfesta` | DFesta 버튼 입력 |
| GET | `/api/game/state` | Python 게임 snapshot |
| GET | `/api/orbs` | 등록된 오브 목록 |
