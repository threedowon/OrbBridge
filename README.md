# OrbBridge

물리 오브 테이블과 Admin을 연결하는 Python 런타임이다. Software Admin에서
도트매트릭스 화면과 테이블을 시뮬레이션하고 운영 상태를 모니터링할 수 있다.

## 구조

```text
OrbBridge/
├── server/                  공연 중 실행되는 Python 본체
│   ├── launcher.py          전체 실행 진입점
│   ├── main.py              모듈 연결과 게임 이벤트 처리
│   ├── data_store.py        설정·오브 매핑 입출력
│   ├── voice_catalog.py     voice 표시색·이름 메타데이터
│   ├── app_paths.py         개발판·배포판 경로 처리
│   ├── game/                게임 규칙과 진행
│   │   ├── game_engine.py
│   │   ├── game_runtime.py
│   │   ├── scheduler.py
│   │   └── melody_manager.py
│   ├── inout/               외부 장치 송수신
│   │   ├── serial_io.py
│   │   ├── matrix_display.py
│   │   └── midi_out.py
│   └── web/                 브라우저 통신
│       ├── web_server.py
│       └── websocket_service.py
├── admin/                   운영·시뮬레이션 UI
│   ├── hardware/            하드웨어 진단 Admin
│   ├── software/            게임·이벤트·MIDI Admin
│   └── simulation/
│       ├── screen/          도트매트릭스 화면 시뮬레이션
│       └── table/           하드웨어 없는 테이블 입력
├── tools/
│   ├── orb_register.py      DS2431 오브 등록
│   ├── melody_register.py   멜로디·게임 규칙·순서 편집
│   ├── midi_debug.py        MIDI 포트·voice 출력 진단
│                           하드웨어 진단은 메인 /admin에 통합
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
| `http://127.0.0.1:8080/admin` | 하드웨어·셀·슬레이브 진단 |
| `http://127.0.0.1:8080/admin/software` | Screen·Table 시뮬레이션과 게임·이벤트·MIDI 통합 |

macOS에서는 프로젝트 루트의 `start.command`를 실행해도 된다. 실행 스크립트는
의존성을 자동 설치하지 않으므로 처음 한 번은 위 설치 명령이 필요하다.
Windows에서는 `start.bat`를 실행하면 하드웨어 Admin과 소프트웨어 Admin 순서로
열린다.

## 게임 계약

상태는 다음 세 개만 사용한다.

```text
READY → RUNNING → RESETTING → READY
```

- `start`: 빈 테이블에서 `READY → RUNNING`
- 정답 후 DFesta 기준 충족, Idle, TimeOver, `end`, `reset`:
  `RUNNING → RESETTING`
- 오브와 tray를 모두 제거: `RESETTING → READY`
- 게임 제어 이벤트: `start`, `end`, `reset`
- 버튼 이벤트: `listen`, `play`, `dfesta`
- 종료 사유: `complete`, `idle`, `time_over`, `manual`

게임 시작은 tray의 정상 배치로 실행한다. Admin에는 tray ON/OFF 토글, `reset`, 자동완성,
모든 오브 회수를 제공한다. `end`는 시리얼·HTTP 외부 제어 호환을 위해 유지한다.

정리 완료 후 LED는 GOODBYE → 암전 → PLACE TRAY 순서로 표시하며,
다음 tray가 놓이면 대기 표시를 중단하고 새 게임을 시작한다.
[Tray 시리얼 규약·표시 시간·펌웨어 적용](docs/dotmatrix-status.md).

## 설정

`data/config.json`이 유일한 실행 설정이다.

```json
{
  "current_stage": 1,
  "melody": "dynamite",
  "melody_rotation": {
    "mode": "random",
    "playlist": []
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
| `melody_rotation.mode` | `random`: 매 게임 무작위(연속 중복 가능), `sequence`: 순회, `fixed`: 지정 곡 고정 |
| `melody_rotation.playlist` | 순회 우선순서. 빈 배열이면 폴더 전체 사용. 삭제된 곡은 제외하고 새 곡은 자동 추가 |
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

DFesta는 곡의 `steps[].harmony`에 지정한 두 화음 성부와 멜로디를 함께
재생한다. 기본 인원 비중은 멜로디/화음 A/화음 B = 70/15/15이며, 멤버의
성부는 재생마다 무작위로 배정한다. 허용 음은 기존 C4~C5의 8개이고
loopMIDI/Ableton 설정은 그대로 사용한다. 화음이 없는 기존 JSON은 유니즌으로
재생한다. [합창 데이터·편곡·박자 검토](docs/dfesta-choir.md).

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
Melody Register는 12 step 피아노롤에서 클릭·드래그로 음정과 길이를 편집한다.
Triplet, Snap to grid, velocity 막대와 기준음 미리듣기를 지원하며, step의
velocity는 DFesta의 멜로디·화음 멤버 모두에 동일하게 적용된다.
[피아노롤 사용법과 JSON 형식](docs/melody-piano-roll.md).

## 하드웨어 진단과 MIDI 준비

```cmd
py -m tools.midi_debug --list
```

하드웨어 진단은 메인 `/admin`에서 실행한다. Master ON/OFF, COM 포트 선택,
생존 상태와 원시 로그를 게임과 함께 사용한다. 사용법: [하드웨어 진단](docs/hardware-debug.md).
loopMIDI 포트 복구 파일은
`audioengine/loopmidi_portsetup.reg`이다. MIDI 단음·8음 순회·전체 voice 동시
테스트는 `/admin/software`의 MIDI 진단에서도 실행할 수 있다.
Play/DFesta의 지연 수치는 같은 패널에서 확인하고 **타이밍 기록 저장**으로
마지막 재생의 JSON을 받을 수 있다. [MIDI 타이밍 조사·재검증](docs/midi-timing.md).
장치 목록과 멤버 채널은 연결 시 읽는다. loopMIDI 장치나 voice 라벨을 변경한
뒤에는 재생을 멈추고 **포트 재연결**을 누른다.

## 웹 동기화

- UI → Python: HTTP `POST /api/...`
- Python → Admin 시뮬레이션·제어 UI: WebSocket `8765`
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
