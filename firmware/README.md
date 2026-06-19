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

## Master (`master/master.ino`)

- RS485 수신 → PC USB `Serial` (115200) relay
- 버튼: GPIO **41** listen, **42** play → `{"type":"game",...}`
- LED: 부팅 후 **초록**, slave JSON 수신 시 **노란** 깜빡

### PC 연결

Master USB → `config.json` `serial.port`  
`bridge.py` + `main.py` + `/screen` start

---

## Slave (`slave/slave.ino`)

- 16× OneWire (GPIO 3–18 등, 기존과 동일)
- RS485로 `added` / `removed` JSON → Master
- 오브 감지 시 LED **빨강** 깜빡
- `SLAVE_ID`: 보드마다 1, 2, 3…

Slave USB는 PC에 연결하지 않음 (디버그 시 `DEBUG_USB 1`).

---

## 전체

```
[Slave] ──RS485 9600──► [Master] ──USB──► PC
                           [41 listen][42 play]
```
