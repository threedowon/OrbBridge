# OrbBridge 펌웨어

## 공통 RS485 (Master / Slave 동일)

| | ESP32-S3 |
|---|----------|
| **TX** (→ MAX485 DI) | GPIO **20** |
| **RX** (← MAX485 RO) | GPIO **21** |
| **RGB LED** | GPIO **48** |
| **속도** | **57600** 8N1 (신호 품질 문제로 115200→57600. 여전히 깨지면 38400) |

DE/RE 핀은 **사용 안 함** (자동 방향(auto-direction) 트랜시버 가정 — 반이중 에코는 펌웨어가 무시).

```
MAX485 RO ──► GPIO 21 (RX)
MAX485 DI ◄── GPIO 20 (TX)
A, B ──────── RS485 버스 (Master ↔ Slave, 양 끝 120Ω 종단)
GND 공통
```

> **종단저항**: 115200에서는 버스 물리 양 끝에 **120Ω**씩 넣고 분기선(stub)은 짧게.

---

## 통신 방식 — 폴링(Polling)

버스에 **한 번에 하나만** 말하도록 강제해 충돌을 원천 차단한다.

- **Master 가 slave 를 순번 호출**: `P<id>\n` (예: `P6\n`) 전송.
- **호출받은 slave 만 응답**, 나머지는 침묵 → 동시 송신 불가.
- Slave 는 오브 감지 시 즉시 보내지 않고 **큐에 적재**했다가, 자기 차례에 몰아서 보고.
- 응답 끝은 **터미네이터 `0x04`** 로 표시. master 는 이걸로 "응답 완료 + 생존" 판정,
  무응답 slave 는 `POLL_IDLE_MS`(≈6ms) 후 건너뜀.
- **반이중 에코**(자기 전송이 되돌아옴)는 파서가 무시 — slave 는 `P` 로 시작하는 줄만,
  master 는 `{` 로 시작하는 줄(=PC relay)과 `0x04`(=완료)만 처리.

| 방향 | 프레임 | 의미 |
|---|---|---|
| master → bus | `P<id>\n` | "slave `<id>`, 보고해" |
| slave → bus | `{"type":"added"/"removed",...}\n` | 큐에 쌓인 이벤트들 |
| slave → bus | `{"type":"heartbeat","slave":<id>,"state":<16bit>}\n` | 상태(주기적, 기본 1s) |
| slave → bus | `0x04 \n` | 응답 끝(터미네이터) — master 가 소비, PC 로 relay 안 함 |

**PC(main.py/debug.py)는 무변경** — master 가 slave 의 JSON 을 그대로 USB relay 하므로
PC 가 보는 줄 포맷은 이전과 동일하다. heartbeat 의 `state` 비트마스크는 PC 가 유실
이벤트를 자가복구(reconcile)하는 데 계속 사용된다.

### 지연(latency)

지연 ≈ "내 차례 기다리기" = 최대 한 폴링 사이클. 6개 slave·57600에서 한 사이클
대략 수십 ms → 체감상 즉각. slave 가 **OneWire 버스 하나 읽을 때마다 RS485 수신을
확인(인터리브)** 하므로 응답 턴어라운드는 ~1ms. (충돌은 폴링으로 baud 와 무관하게 0)

---

## Master (`master/master.ino`)

- RS485로 `POLL_IDS` 를 순번 폴링 → slave 응답 JSON 을 PC USB `Serial`(115200)로 relay
- **`POLL_IDS`** 를 실제 보드들의 `SLAVE_ID` 와 일치시킬 것 (기본 `{1,2,3,4,5,6}`)
- 버튼: GPIO **41** listen, **42** play → `{"type":"game",...}` (폴 대기 중에도 반응)
- LED: 부팅 후 **초록**, slave **이벤트** 수신 시 **노란** 깜빡(논블로킹). heartbeat 는 조용히.

### PC 연결

Master USB → `config.json` `serial.port` · `main.py` 실행 · `/screen` start

---

## Slave (`slave/slave.ino`)

- 16× OneWire (GPIO 3–18 등, 기존과 동일)
- 오브 감지 → **큐 적재**, `P<SLAVE_ID>` 폴 받으면 큐 flush + (주기적)heartbeat + 터미네이터
- OneWire 스캔과 스캔 **사이에 RS485 수신 확인** → 폴 응답 즉시(턴어라운드 최소)
- 온보드 RGB(GPIO 48): 오브 감지 시 **빨강** 깜빡 (논블로킹)
- **NeoPixel 링**: GPIO **38** 한 선에 12 LED 링을 직렬 체인 (감지 즉시 점등 — 폴과 무관)
  - DS2431 버스 1개 = 링 1개 (버스 `i` → LED `[i*12 … i*12+11]`)
  - 핀 선택(S3): **38 / 39 / 40** 안전. **35~37은 Octal PSRAM(N16R8 등) 모듈이면 사용 불가**
- `SLAVE_ID`: 보드마다 1, 2, 3… (master `POLL_IDS` 와 일치)
- 큐가 넘쳐 이벤트가 드롭돼도, heartbeat 의 `state` 마스크로 PC 가 자가복구 → 안전망

Slave USB는 PC에 연결하지 않음 (디버그 시 `DEBUG_USB 1`).

---

## 전체

```
        ┌── P1 ─►[Slave1] ─(큐 응답)─┐
        ├── P2 ─►[Slave2] ──────────┤
[Master]┤   …  순번 폴링            ├─ RS485 115200 (한 번에 하나만 응답)
        └── P6 ─►[Slave6] ──────────┘
   │
   └── USB 115200 ──► PC (main.py)
   [41 listen][42 play]
```

## baud 변경 시 주의

`RS485_BAUD` 는 master.ino·slave.ino **양쪽 모두 같은 값**이어야 한다(한 대라도 다르면
그 보드만 통신 두절). PC↔master USB(115200)와는 별개다. 변경 시 **모든 보드 재플래시**.

### 바이트 깨짐(신호 무결성) 트러블슈팅
단일 메시지 안의 바이트가 깨지면(`"heartbeat"`→`"hea\`eat"` 등) baud 가 하드웨어에
비해 빠른 것이다. 순서대로:
1. **종단저항 120Ω** — 버스 물리 양 끝에. 무종단이면 반사로 고속에서 깨진다.
2. **baud 낮추기** — `RS485_BAUD` 57600 → 38400 (폴링이라 충돌은 그대로 0, 지연만 소폭↑).
3. 그래도면 저가형 auto-direction 모듈 문제 → MAX13487 등 **정품 방향제어 칩**으로 교체.
