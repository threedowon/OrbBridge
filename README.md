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

`data/config.json`의 `hint_led_fade_ms`는 힌트 음이 끝난 후 LED 잔상이 사라지는 시간이다.
기본값 `600`은 0.6초이며 `0`~`3000` 사이 정수로 설정한다 (`0`은 잔상 없음).
설정 변경 후 서버를 재시작한다. 이 설정을 지원하는 마스터·슬레이브 펌웨어를 한 번 업로드한 뒤에는 값만 바꿔도 된다.
재생 명령에 잔상 시간을 함께 전달하므로 다음 힌트부터 적용된다.

트레이 안착으로 게임이 시작되면 전체 슬롯 링이 금색으로 한 번 켜진다.
`game_rules.initial_hint_delay_s`의 남은 시간보다 1초 앞서 꺼지도록 마지막 최대 0.8초 동안 페이드아웃한다.
대기시간이 1초 이하이면 이 연출을 생략하며, 제거·리셋 시에는 즉시 해제한다.

디페스타 재생에서는 현재 박자의 열만 정답 음까지 금색으로 채운다.
디페스타 버튼 활성화 대기 중에는 정답 슬롯만 2초 주기로 약한 금색과 밝은 금색을 오간다.
정답 조건이 깨지면 일반 표시로 복귀하고, 재생 중에는 재생 연출을 우선한다.
예를 들어 솔이면 도·레·미·파·솔이 동시에 켜지고, 라·시·높은 도와 다른 열은 꺼진다.
쉼표에서는 소등하며 종료·취소 시 일반 표시로 돌아간다. 기존 음원과 화음 재생은 변경하지 않는다.

`data/config.json`의 `hall_sensor_count`는 트레이 안착에 필요한 홀센서 수다.
기본값 `2`는 두 센서 모두 감지해야 하고, `1`은 둘 중 하나만 감지해도 안착으로 처리한다.
변경 후 서버를 재시작한다. 현재 마스터 펌웨어는 그대로 사용하며 Software 탭의
트레이 시뮬레이션과 통신 끊김 처리는 바뀌지 않는다. 고장 센서가 항상 감지 상태로
고정되면 트레이 제거를 판단할 수 없으므로 해당 센서의 입력 상태를 먼저 점검한다.

실행 설정은 두 파일로 나뉜다.

- `data/config.json`: 게임·통신 속도·디스플레이 문구 등 공통 설정.
- `data/ports.json`: 이 PC의 COM 포트. Git에서 제외되므로 다른 PC의 변경으로 덮이지 않는다.
- `data/ports1.json`: Git에 포함하는 예시 설정. 다른 PC에서는 이 파일을 같은 폴더에 `ports.json`으로 복사한 뒤 COM 번호를 수정한다. 예시 파일은 그대로 남겨 둔다.

`ports.json`에는 다음 두 값만 넣는다. 포트를 바꾼 뒤 서버를 재시작한다.

```json
{
  "serial": {"port": "COM19"},
  "matrix_display": {"port": "COM3"}
}
```

첫 실행에 로컬 파일이 없으면 자동 생성한다. 기존 config에 포트가 있으면 그 값을 가져오고,
없으면 COM19 / COM3으로 시작하므로 각 PC에서 실제 번호로 수정한다.
로컬 파일의 포트가 우선하며 공통 설정을 저장할 때는 포트 값을 제외한다.

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
    "baudrate": 115200
  },
  "matrix_display": {
    "enabled": true,
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

### ORB 버전별 배정

ORB 탭에서 **오리지널 버전 / 남자 아이돌 버전**을 선택한다. 전환 시 게임과
재생을 중단하고 점검 모드로 들어가므로, 운영을 재개할 때는 **점검 종료**를 누른다.
등록된 오브 ID를 재사용하며, 버전 전환만을 위해 오브를 다시 읽힐 필요는 없다.
실물 점검을 하려면 전환 후 오브를 뺐다가 다시 올린다.

- 오리지널은 전체 48명, 남자 아이돌은 BTS·CORTIS·ENHYPEN·RIIZE·SKZ·TXT의 38명이다.
- 처음 만든 버전은 모든 등록 ID를 무작위로 고르게 배정한다. 192개 기준 남자
  아이돌 버전은 멤버당 5~6개이며, 저장된 버전을 다시 선택하면 이전 배정을 유지한다.
- **현재 버전 랜덤 재배정**은 선택한 버전만 다시 섞는다. 기존 오리지널 배정은
  첫 전환 시 그대로 보존한다. 신규 오브는 **등록 시작**으로 추가한다.
- 버전 기능을 처음 사용한 뒤에는 `data/orb_mapping.versions.json`에 현재 버전과
  모든 버전의 배정표가 저장되며 이 파일을 우선 사용한다. 기존 `orb_mapping.json`은
  최초 원본으로 남는다. PC를 옮기거나 백업할 때 `data/` 전체를 복사한다.
- 배정 변경 전 `data/orb_backups/`에 백업한다. 고장 난 오브의 ID를 교체하면
  저장된 다른 버전에서도 ID를 바꾸고 각 버전의 음성 배정은 이어받는다.

이 설정은 오브의 음성 배정을 바꾸며 게임의 곡 선택 설정은 변경하지 않는다.
