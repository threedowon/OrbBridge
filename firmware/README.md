# OrbBridge 펌웨어

## 공통 RS485 (Master / Slave 동일)

실물 테스트 기준:

| | ESP32-S3 |
|---|----------|
| **TX** (→ MAX485 DI) | GPIO **20** |
| **RX** (← MAX485 RO) | GPIO **21** |
| **RGB LED** | GPIO **48** |
| **속도** | **9600** 8N1 |

DE/RE 핀은 테스트 코드와 같이 **사용 안 함** (모듈에 RE·DE가 묶여 있거나 자동 방향 모듈).

```
MAX485 RO ──► GPIO 21 (RX)
MAX485 DI ◄── GPIO 20 (TX)
A, B ──────── RS485 버스 (Master ↔ Slave)
GND 공통
```

---

## 통신 방식: Master 폴링 (충돌 방지)

여러 slave 가 공유 RS485 버스에서 **동시에 전송하면 프레임이 깨진다**(버스 충돌).
그래서 slave 가 알아서 보내지 않고, **master 가 하나씩 호출(poll)** 하고 호출받은
slave 만 응답한다 → 버스에 말하는 놈이 항상 하나뿐이라 충돌이 없다.

| 방향 | 프레임 | 의미 |
|---|---|---|
| Master → bus | `P<id>\n` (예 `P3\n`) | "slave 3, 보고해" |
| Slave → bus | `{"type":"added"/"removed",...}` 줄들 | 그동안 큐에 쌓인 이벤트 |
| Slave → bus | `{"type":"heartbeat","slave":<id>}` | 응답 끝 표시 **겸** 생존신호 |

- slave 는 오브 감지 시 **즉시 링 LED 만 갱신**하고 이벤트는 **큐에 적재** → 자기 차례에 몰아서 전송
- master 는 slave 응답 JSON 을 **그대로 PC 로 relay** (PC 쪽 코드 변경 없음)
- heartbeat 는 slave 당 **3초에 한 번만** PC 로 relay (로그 오염 방지, debug 도구 online 판정용)
- 폴링 대상 ID: master 의 `POLL_ID_MIN`~`POLL_ID_MAX` (기본 1~6). 실제 보드 ID 에 맞춰 조정
- **지연이 크면** master·slave 의 `RS485_BAUD` 를 함께 올리면 됨(예: 57600 → 폴 사이클 ~6배 빨라짐)

---

## Master (`master/master.ino`)

- slave 를 `POLL_ID_MIN`~`POLL_ID_MAX` 순서로 폴링 → 응답 JSON 을 PC USB `Serial`(115200) relay
- 버튼: GPIO **41** listen, **42** play → `{"type":"game",...}` (USB 로 직접, RS485 무관)
- LED: 부팅 후 **초록**, slave 이벤트(added/removed) relay 시 **노란** 깜빡
- heartbeat 는 slave 당 3초에 한 번만 relay (`HEARTBEAT_FORWARD_MS`)

### PC 연결

Master USB → `config.json` `serial.port`  
`bridge.py` + `main.py` + `/screen` start

---

## Slave (`slave/slave.ino`)

- 16× OneWire (GPIO 3–18 등, 기존과 동일)
- 오브 감지 시: 링 LED 즉시 갱신 + 이벤트 **큐 적재** → master 폴 요청 시 `added`/`removed` JSON 전송
- master 폴 요청(`P<id>\n`)이 자기 `SLAVE_ID` 면 응답, 아니면 무시
- 온보드 RGB(GPIO 48): 오브 감지 시 **빨강** 깜빡
- **NeoPixel 링**: GPIO **38** 한 선에 12 LED 링을 직렬 체인
  - DS2431 버스 1개 = 링 1개 (버스 `i` → LED `[i*12 … i*12+11]`)
  - 예: 0번 버스 감지 → LED 0~11 **초록** 점등, 제거 시 소등
  - 상수 `LEDS_PER_RING`(12) / 색상(`ring.Color`)으로 조정
  - 핀 선택(S3): **38 / 39 / 40** 안전. **35~37은 Octal PSRAM(N16R8 등) 모듈이면 사용 불가**
- `SLAVE_ID`: 보드마다 1, 2, 3…

Slave USB는 PC에 연결하지 않음 (디버그 시 `DEBUG_USB 1`).

---

## 전체

```
             poll "P<id>\n"
[Master] ──────────────────────► [Slave]
   ▲   ◄────────────────────────    (해당 id 만 응답)
   │      added/removed/heartbeat
   │ USB
   ▼
  PC                        [41 listen][42 play]
```
