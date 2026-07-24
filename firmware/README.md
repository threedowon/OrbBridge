# OrbBridge 펌웨어

## 공통 RS485 (Master / Slave 동일)

| | ESP32-S3 |
|---|----------|
| **TX** (→ MAX485 DI) | GPIO **20** |
| **RX** (← MAX485 RO) | GPIO **21** |
| **RGB LED** | GPIO **48** |
| **속도** | **9600** 8N1 |

DE/RE 핀 노출 안 됨 (auto-direction 모듈). **이 모듈은 양방향(master 송신)이 불안정**해서,
master 는 **송신하지 않고 듣기만** 하는 단방향 구조로 간다(단방향은 9600에서 깨끗함).

```
MAX485 RO ──► GPIO 21 (RX)
MAX485 DI ◄── GPIO 20 (TX, master 는 미사용)
A, B ──────── RS485 버스 (Master ↔ Slave, 양 끝 종단 100~120Ω)
GND 공통
```

---

## 통신 방식 — 이벤트 + Carrier-Sense (단방향)

**master 는 듣기만, slave 만 자율 전송.** master 가 RS485 로 송신하지 않으므로 방향 전환이
없어 신호가 깨끗하다(양방향 폴링은 이 auto-direction 모듈이 못 버텨서 폐기).

충돌 방지는 **slave 쪽에서**:
- slave 는 전송 전에 **버스가 조용한지 듣고**(carrier-sense, `BUS_IDLE_MS`), 사용 중이면
  **ID+랜덤 백오프** 후 재시도 → 동시 전송(충돌) 최소화.
- 그래도 드물게 충돌해 이벤트가 유실되면, heartbeat 의 **state 마스크**로 PC 가
  자가복구(reconcile) → 3~6초 안에 화면 동기화(안전망).

| 방향 | 프레임 | 의미 |
|---|---|---|
| slave → bus | `{"type":"added"/"removed","slave":<id>,"cell":<n>,"id":"<uid>"}\n` | 오브 감지 즉시 |
| slave → bus | `{"type":"heartbeat","slave":<id>,"state":<16bit>}\n` | 상태(기본 2s 주기) |

**PC(main.py/debug.py)는 무변경** — master 가 slave JSON 을 그대로 USB relay. heartbeat 의
`state` 비트마스크(bit i = cell i+1 점유)가 자가복구의 핵심.

3중 방어: ① 단방향(신호 깨끗) → ② carrier-sense(충돌 감소) → ③ 자가복구(남은 유실 복구).

---

## Master (`master/master.ino`) — 듣기만

- RS485 를 듣기만 하며 slave JSON 을 PC USB `Serial`(115200)로 relay. **RS485 송신 안 함.**
- 버튼: GPIO **41** listen, **42** play → `{"type":"game",...}`
- LED: 부팅 후 **초록**, slave **이벤트** 수신 시 **노란** 깜빡. heartbeat 는 조용히.
- 진단: 2초마다 `#master alive relay=<n>` 을 PC 로 (main.py 가 `[master] ...` 로 로그).

### PC 연결
Master USB → `config.json` `serial.port` · `main.py` 실행 · `/screen` start

---

## Slave (`slave/slave.ino`) — 자율 전송 + carrier-sense

- 16× OneWire (GPIO 3–18 등)
- 오브 감지 → 즉시 **carrier-sense 전송**(버스 듣고 조용하면 송신, 아니면 백오프)
- heartbeat(+state) 를 **`HEARTBEAT_MS`(기본 2s)** 마다 전송, 보드마다 위상 어긋나게 초기화
- 온보드 RGB(GPIO 48): 오브 감지 시 **빨강** 깜빡 (논블로킹, 전송과 무관하게 즉시)
- **NeoPixel 링**: GPIO **38** 체인, 12 LED × 16. 감지 즉시 점등(전송과 무관)
  - 핀 선택(S3): **38 / 39 / 40** 안전. **35~37은 Octal PSRAM 모듈이면 사용 불가**
- **`SLAVE_ID`: 보드마다 1, 2, 3… 다르게** (같으면 두 보드가 같은 id 로 보고돼 혼란)
- 충돌로 이벤트가 유실돼도 heartbeat `state` 마스크로 PC 가 자가복구

Slave USB는 PC에 연결하지 않음 (디버그 시 `DEBUG_USB 1`).

### Carrier-sense 튜닝
- `BUS_IDLE_MS`(기본 4) — 이만큼 무신호면 버스 비었다고 판단
- `CS_MAX_TRIES`(기본 20) — 버스 확보 재시도. 실패 시 이번 전송 포기(자가복구가 메꿈)
- 충돌이 잦으면 백오프 폭을 늘리거나 `HEARTBEAT_MS` 를 키운다.

---

## 전체

```
[Slave1] ─┐  (각자 감지 즉시 자율 전송,
[Slave2] ─┤   전송 전 버스 듣고 양보 = carrier-sense)
   …      ├─ RS485 9600 (단방향: slave → master) ─► [Master] ─ USB 115200 ─► PC
[Slave6] ─┘                                          [41 listen][42 play] (듣기만)
```

## baud / 신호 트러블슈팅

`RS485_BAUD` 는 master·slave **모두 같은 값**(한 대라도 다르면 그 보드만 두절).
PC↔master USB(115200)와는 별개. 변경 시 **모든 보드 재플래시**.

- 단방향인데도 바이트가 깨지면 → **종단저항**(양 끝 100~120Ω)·**공통 GND**·A/B 배선 확인
- 충돌(두 slave 겹침)이 잦으면 → carrier-sense 백오프↑ 또는 `HEARTBEAT_MS`↑
- 무선처럼 완전 무충돌·고속이 필요하면 → **DE/RE 핀 나온 모듈**로 교체 후 폴링 복원 가능
