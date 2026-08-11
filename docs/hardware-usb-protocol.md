# Hardware USB Protocol

OrbBridge Python과 `master`, `dotmatrix` ESP 보드는 115200 baud USB Serial에서
한 줄당 JSON 객체 하나를 주고받는다. COM 번호는 설정에 저장하지 않는다.

## Handshake

Python은 검색한 COM에 같은 nonce를 포함한 `hello`를 보낸다.

```json
{"cmd":"hello","protocol":"orbbridge","version":1,"nonce":"a17f23c1"}
```

각 보드는 자신의 고정 역할을 응답한다.

```json
{"type":"hello","protocol":"orbbridge","version":1,"role":"master","device_id":"7CDFA1001234","fw":"0.1.0","nonce":"a17f23c1"}
```

```json
{"type":"hello","protocol":"orbbridge","version":1,"role":"dotmatrix","device_id":"7CDFA1005678","fw":"0.1.0","nonce":"a17f23c1"}
```

`role`은 필수이며 `master` 또는 `dotmatrix`만 사용한다. `device_id`는 특정
보드를 설정에 강제하지 않고 Admin 표시와 동일 역할 중복 감지에만 사용한다.
ESP32의 고유 MAC을 문자열로 보내면 된다.
Master 구현은 `firmware/master/master.ino`에 포함되어 있다. Dot Matrix의 최종
렌더 펌웨어가 완성되기 전에는
`firmware/dotmatrix/serial_handshake_test/serial_handshake_test.ino`로 자동 식별과
재연결을 먼저 시험할 수 있다.

## 연결 확인

Python은 연결된 두 장치에 주기적으로 `ping`을 보낸다.

```json
{"cmd":"ping","protocol":"orbbridge","version":1}
```

보드는 다음 한 줄을 응답한다.

```json
{"type":"pong"}
```

응답이 끊기거나 USB 쓰기가 실패하면 해당 COM을 닫고 자동 검색을 다시 시작한다.

## Master → Python

기존 게임 JSON 형식을 유지한다.

```json
{"type":"added","slave":1,"cell":1,"id":"AA:00:00:00:00:00:00:01"}
{"type":"removed","slave":1,"cell":1,"id":"AA:00:00:00:00:00:00:01"}
{"type":"game","action":"start"}
{"type":"heartbeat","slave":1,"occupancy_mask":3}
```

## Python → Dot Matrix

밝기와 화면 명령을 사용한다. `seq`는 로그와 메시지 순서 확인용이며 ACK 재전송
큐는 사용하지 않는다.

```json
{"cmd":"brightness","value":64}
{"cmd":"clear","seq":1}
{"cmd":"show","seq":2,"view":"game_status","transition":"fade","state":"RUNNING","stage":1,"melody":"dynamite","seconds":120}
```

현재 `game_status`는 Python 연동 확인용 기본 view이다. 최종 관객용 표시 형식은
`matrix_display.py`에서 새 view로 확장하고, USB 연결 코드는 변경하지 않는다.
초 단위 갱신은 `transition: none`, 상태 전환은 `transition: fade`를 사용한다.
Dot Matrix가 재연결되면 Python이 밝기와 최신 화면을 한 번 다시 보낸다.
