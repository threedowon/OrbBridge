# OrbBridge 진단 도구 (Diagnostics)

마스터 COM 포트를 직접 열어 통신을 모니터링하고, **어떤 슬레이브가 붙어
있는지 / 통신이 잘 되는지 / 마스터가 몇 번 COM 포트인지**를 웹 대시보드로
보여줍니다.

## 실행

```
cd debug
python debug.py            # server/config.json 의 serial.port 로 자동 연결
python debug.py --port COM23
python debug.py --no-open  # 포트 안 열고 목록만 확인
```

또는 `debug.bat` 더블클릭.

브라우저: **http://127.0.0.1:8090/**

## ⚠️ COM 포트는 하나만 열 수 있습니다

`server/main.py` 가 실행 중이면 이미 마스터 COM 포트를 점유하고 있어서
이 도구가 같은 포트를 열지 못합니다. **진단할 때는 main.py 를 잠깐 종료**하고
이 도구를 실행하세요. (반대도 마찬가지)

## 하드웨어 구조 (진단 대상)

```
[Slave] ──RS485(A/B, 9600)──► [Master] ──USB Serial──► PC (COM 포트)
```

- **마스터만 PC 에 COM 포트로 잡힙니다.** (`master.ino`, USB CDC)
- **슬레이브는 COM 포트로 안 잡힙니다.** RS485 로 마스터에만 연결됩니다.
  슬레이브는 자기 `SLAVE_ID`(`slave.ino` 의 `#define SLAVE_ID`)를 JSON 에
  실어 보내고, 이 도구는 마스터를 통해 들어온 JSON 의 `slave` 필드로
  슬레이브를 감지합니다.
- 슬레이브 USB 를 디버그용으로 직접 꽂으면(`DEBUG_USB 1`) 별도 COM 으로도
  잡히지만, 정상 운영에서는 하지 않습니다.

## 슬레이브 상태 판정 방식 (하트비트)

각 슬레이브는 `slave.ino` 의 `sendHeartbeat()` 로 **약 3초마다**
`{"type":"heartbeat","slave":ID}` 를 RS485 로 보냅니다(`HEARTBEAT_MS` 상수).
오브가 하나도 없어도 신호가 오므로 대시보드가 실시간으로 판정합니다.

- **🟢 ONLINE** — 최근 `ONLINE_TIMEOUT_S`(기본 8초) 안에 하트비트 수신.
- **🔴 OFFLINE** — 8초 넘게 하트비트 없음(슬레이브 정지 / RS485 단선 / 전원).
- 슬레이브마다 하트비트 **위상을 어긋나게**(`SLAVE_ID` 기반) 초기화해
  RS485 버스 충돌을 줄입니다. 하트비트 몇 개를 놓쳐도 8초 창 덕분에
  깜빡이지 않고 online 을 유지합니다.

> 구 펌웨어(하트비트 없음)를 물리면 대시보드가 자동으로 "이벤트 최근성"
> 방식으로 표시합니다(하트비트 없음 표기). 이때는 오브를 올렸다 빼서 확인하세요.

### 펌웨어·서버 쪽 변경 요약
- `firmware/slave/slave.ino` — `HEARTBEAT_MS`, `sendHeartbeat()`, loop 주기 송신
- `firmware/master/master.ino` — 하트비트는 LED 깜빡 없이 조용히 relay
- `server/main.py` — 하트비트로 슬레이브별 online/offline 을 판정해 `/api/status`
  (`slaves` 필드)로 노출. `/admin` 이 이를 표시(게임 로직 자체와는 무관).

## 대시보드 구성

| 영역 | 내용 |
|---|---|
| 마스터 | 연결된 COM 포트, 열림/끊김, 마지막 수신 시각, 수신 줄 수(전체/JSON/기타) |
| COM 포트 선택 | PC 의 모든 시리얼 포트 목록, ESP32 추정 표시, 포트 전환 |
| 슬레이브 | 감지된 `SLAVE_ID` 별 최근 활동, 메시지/added/removed 수, 활성 셀 |
| 활성 셀 | 지금 오브가 올려진 셀 (slave·cell·orb id) |
| 원시 로그 | 시리얼로 들어오는 raw 줄 실시간 (JSON=초록, 기타=회색) |

## 게임 중 실시간 모니터링 (구현됨)

이 진단 도구는 마스터 COM 포트를 직접 열기 때문에 `main.py` 와 **동시에 쓸 수
없습니다**. 그래서 게임 실행 중 슬레이브 생존 확인은 `main.py` 쪽에 붙여 두었습니다.

- `server/main.py` 가 heartbeat 로 슬레이브별 online/offline 을 판정해
  `GET /api/status` 의 `slaves` 배열로 노출합니다(추가 시리얼 연결 없음 → 지연 0).
- `/admin` 사이드 패널의 **"슬레이브 상태"** 에서 실시간으로 보입니다.
- 이 진단 도구는 여전히 **오프라인 심층 진단**(원시 로그, 포트 탐색, 노이즈/
  JSON 카운트 등)에 사용하세요.

## 향후 개선 아이디어 (선택)

- **하트비트 주기 조정**: `slave.ino` 의 `HEARTBEAT_MS` 와 판정 창
  (`ONLINE_TIMEOUT_S` / `main.py` 의 `SLAVE_ONLINE_TIMEOUT_S`)을 함께 조정하면
  민감도를 바꿀 수 있습니다.
