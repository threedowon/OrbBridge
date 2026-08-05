# M4 Python 직접 WebSocket

Python 프로세스가 브라우저 이벤트를 직접 전송하고, WebSocket 연결·재연결
시점마다 전체 snapshot을 보낸다. `screen`, `table`, `admin`은 동일한 기준
상태를 렌더링하는 웹 클라이언트다.

## 실행 경로

```text
Serial / HTTP
  → GameRuntime / Scheduler
  → Python WebSocket 서비스
  → screen / table / admin
```

## 전송 형식

실시간 이벤트는 브라우저 공통 형식을 사용한다.

```json
{
  "type": "event",
  "address": "/game/state",
  "args": ["RUNNING", "", 3, 119.4, 29.4, "READY"]
}
```

모든 새 연결은 이후 실시간 이벤트보다 먼저 snapshot을 받는다.

```json
{
  "type": "snapshot",
  "snapshot": {
    "schema_version": 2,
    "game": {},
    "cells": {},
    "status": {},
    "events": []
  }
}
```

snapshot은 현재 상태, 배치된 오브, admin 상태, 최근 이벤트 로그, 활성 재생
식별자를 복구하는 기준 데이터다.

## 브라우저 복구

- `screen`은 재연결 때 오브 소리를 다시 내지 않고 배치만 복구한다.
- `table`은 현재 상태와 배치 셀을 복구한다.
- `admin`은 상태·셀·최근 이벤트를 복구하고, 감소하는 타이머 표시는 기존 HTTP
  갱신을 유지한다.
- HTTP 상태 조회는 서버 시작 경합에 대비한 짧은 보조 경로로 유지한다.

## 설정

세 브라우저의 WebSocket 포트는 모두 `8765`다. 수신 host는 HTTP host를
기본값으로 사용하며 `config.json`의 `websocket.host`로 바인딩 주소만 바꿀 수
있다.
