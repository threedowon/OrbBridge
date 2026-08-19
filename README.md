# OrbBridge

물리 오브 테이블과 Screen·Admin을 연결하는 Python 런타임이다. Admin 한 화면에서
관객 화면, 테이블 시뮬레이터, 운영 제어를 함께 모니터링할 수 있다.

## 구조

```text
OrbBridge/
├── server/                  공연 중 실행되는 Python 본체
│   ├── launcher.py          전체 실행 진입점
│   ├── main.py              모듈 연결과 게임 이벤트 처리
│   ├── game_engine.py       상태·셀·정답·타이머 순수 로직
│   ├── game_runtime.py      입력 Queue와 단일 writer
│   ├── scheduler.py         Listen·Play·DFesta 재생 스케줄
│   ├── serial_io.py         ESP USB 시리얼 입력
│   ├── matrix_display.py    192×32 도트매트릭스 출력
│   ├── midi_out.py          Ableton MIDI 출력
│   ├── web_server.py        HTTP API와 정적 파일 제공
│   ├── websocket_service.py 실시간 이벤트와 재접속 snapshot
│   ├── data_store.py        설정·오브 매핑 입출력
│   ├── melody_manager.py    멜로디 로드·규칙·게임별 순회
│   ├── voice_catalog.py     voice 표시색·이름 메타데이터
│   └── app_paths.py         개발판·배포판 경로 처리
├── web/
│   ├── screen/              관객용 화면
│   └── admin/               통합 모니터
│       ├── control/         운영 상태·로그·제어
│       └── table/           하드웨어 없는 테이블 입력
├── tools/
│   ├── orb_register.py      DS2431 오브 등록
│   ├── melody_register.py   멜로디·게임 규칙·순서 편집
│   ├── midi_debug.py        MIDI 포트·voice 출력 진단
│   └── debug/               마스터·슬레이브 심층 진단
├── data/                    실행 데이터의 단일 저장 위치
│   ├── config.json
│   ├── orb_mapping.json
│   ├── portmapping.json
│   ├── voice_catalog.json
│   └── melodies/
├── firmware/                ESP master·slave·dotmatrix·등록기 펌웨어
├── audioengine/             loopMIDI·Ableton 운영 파일
├── tests/                   자동 회귀 테스트
├── docs/                    M1–M6 설계 기록
└── build/                   PyInstaller 설정
```

## 설치와 실행

```bash
python -m pip install -r requirements.txt
python -m server.launcher
```

브라우저 주소는 다음과 같다.

| 주소 | 역할 |
|---|---|
| `http://127.0.0.1:8080/screen` | 관객용 악보·진행 표시 |
| `http://127.0.0.1:8080/admin` | Screen·Table·Control 통합 모니터 |

macOS에서는 프로젝트 루트의 `start.command`를 실행해도 된다. 실행 스크립트는
의존성을 자동 설치하지 않으므로 처음 한 번은 위 설치 명령이 필요하다.
Windows에서는 `start.bat`를 실행하면 서버, Screen, Admin이 함께 열린다.

## 게임 계약

상태는 다음 세 개만 사용한다.

```text
READY → RUNNING → RESETTING → READY
```

- `start`: 빈 테이블에서 `READY → RUNNING`
- 정답 후 DFesta 기준 충족, Idle, TimeOver, `end`, `reset`:
  `RUNNING → RESETTING`
- 오브를 모두 제거: `RESETTING → READY`
- 게임 제어 이벤트: `start`, `end`, `reset`
- 버튼 이벤트: `listen`, `play`, `dfesta`
- 종료 사유: `complete`, `idle`, `time_over`, `manual`

게임 시작은 Admin에서만 실행한다. Admin에는 `start`, `reset`, 자동완성,
모든 오브 회수를 제공한다. `end`는 시리얼·HTTP 외부 제어 호환을 위해 유지한다.

## 설정

`data/config.json`이 유일한 실행 설정이다.

```json
{
  "current_stage": 1,
  "melody": "dynamite",
  "melody_rotation": {
    "mode": "sequence",
    "playlist": ["dynamite", "Iam", "biplane"]
  },
  "input_mode": "table",
  "web": {
    "host": "127.0.0.1",
    "port": 8080
  },
  "serial": {
    "port": "COM5",
    "baudrate": 115200
  },
  "matrix_display": {
    "enabled": true,
    "port": "COM6",
    "baudrate": 115200
  }
}
```

| 항목 | 값 |
|---|---|
| `input_mode` | `table` 또는 `serial` |
| `melody` | `fixed` 모드의 기본 멜로디 ID |
| `melody_rotation.mode` | `sequence`: 게임마다 다음 곡, `fixed`: 첫 곡 고정 |
| `melody_rotation.playlist` | Melody Register에서 관리하는 게임 순서 |
| `web` | HTTP 바인딩 주소와 포트 |
| `websocket.host` | 선택값이며 없으면 `web.host` 사용 |
| `serial` | ESP 마스터 COM 포트와 baudrate |
| `matrix_display` | 도트매트릭스 사용 여부와 COM 포트 |

`config.table.json`, 별도 debug 설정, `sounds/` 샘플 경로는 사용하지 않는다.

## 데이터

- `data/melodies/*.json`: 판정·화면·재생·게임 규칙의 단일 원본
- `data/orb_mapping.json`: 오브 UID → voice
- `data/portmapping.json`: voice 그룹 → MIDI 포트·채널
- `data/voice_catalog.json`: 화면 표시색·멤버 이름

서버는 게임 시작 직전에 설정과 다음 멜로디를 다시 읽고 `/game/melody`를
`RUNNING` 상태보다 먼저 전송한다. `sequence`는 성공한 start에만 순번을
진행하며 마지막 곡 뒤에는 첫 곡으로 돌아간다.

## 등록 도구

```bash
python -m tools.orb_register
python -m tools.orb_register --list

python -m tools.melody_register
python -m tools.melody_register --list
```

Orb Register는 `data/orb_mapping.json`을 관리한다. Melody Register는
`data/melodies/*.json`의 BPM, 음표, duration, 가사, 게임 규칙과 게임별 재생
순서를 함께 관리한다. Windows 실행 파일은 `tools\*.bat`에 있다.

## 하드웨어 진단과 MIDI 준비

```cmd
py -m tools.debug
py -m tools.midi_debug --list
```

심층 시리얼 진단은 `tools/debug/`에서 실행한다. 게임 런타임과 같은 COM 포트를
동시에 열 수 없으므로 둘 중 하나만 실행해야 한다. loopMIDI 포트 복구 파일은
`audioengine/loopmidi_portsetup.reg`이다. MIDI 단음·8음 순회·전체 voice 동시
테스트는 `/admin`의 MIDI 진단에서도 실행할 수 있다.

## 웹 동기화

- UI → Python: HTTP `POST /api/...`
- Python → Screen·Admin 내부 UI: WebSocket `8765`
- 연결 직후: 게임·멜로디·셀·상태·로그 전체 snapshot
- 이후: 동일 연결에서 실시간 이벤트 순서 보장

주요 API는 다음과 같다.

| 메서드 | 경로 |
|---|---|
| `POST` | `/api/event` |
| `POST` | `/api/game/start` |
| `POST` | `/api/game/end` |
| `POST` | `/api/game/reset` |
| `POST` | `/api/game/listen` |
| `POST` | `/api/game/play` |
| `POST` | `/api/game/dfesta` |
| `POST` | `/api/admin/game/auto-complete` |
| `POST` | `/api/admin/orbs/collect` |
| `GET` | `/api/game/state` |
| `GET` | `/api/melody` |
| `GET` | `/api/status` |
| `GET` | `/api/admin/midi/status` |
| `POST` | `/api/admin/midi/test` |
| `POST` | `/api/admin/midi/stop` |
| `POST` | `/api/admin/midi/reconnect` |

## 검증과 빌드

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q

pyinstaller build/orbbridge.spec --noconfirm
pyinstaller build/orb_register.spec --noconfirm
```

Windows에서는 `build\build_orbbridge.bat`와
`build\build_orb_register.bat`를 사용할 수 있다.

PyInstaller 실행 파일은 옆의 `data/`를 쓰며, 첫 실행에 번들 기본 데이터를
복사한다.
