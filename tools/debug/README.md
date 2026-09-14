# OrbBridge 진단 도구

마스터 COM 포트를 직접 열어 수신 JSON, 슬레이브 하트비트, 셀 점유와 원시
로그를 별도 웹 대시보드로 확인한다.

## 실행

프로젝트 루트에서 다음 명령을 사용한다.

```cmd
py -m tools.debug
py -m tools.debug --port COM23
py -m tools.debug --no-open
```

Windows에서는 `tools\debug\debug.bat`를 더블클릭해도 된다. 대시보드는
`http://127.0.0.1:8090/`에서 열린다.

COM 포트는 한 프로세스만 열 수 있으므로 진단 전 `server.launcher`를 종료해야
한다. 게임 중 간단한 상태 확인은 `/admin`, 오프라인 심층 진단은 이 도구를
사용한다.

## 슬레이브 단독 점검 (`/slave`)

master·RS485 없이 slave 보드 한 장만 USB 로 연결해 조립 직후 점검한다.
`firmware/slave_test/slave_test.ino`를 굽는다. 핀 배치와 RS485 프레임은
`slave.ino`와 같으므로 점검이 끝나면 `slave.ino`를 다시 굽기만 하면 된다.

```cmd
py -m tools.debug --slave COM7
```

대시보드는 `http://127.0.0.1:8090/slave`에서 열리며 master 진단 페이지의
헤더 링크로도 이동할 수 있다. 확인 항목은 다음과 같다.

| 항목 | 판정 방법 |
|---|---|
| 펌웨어·DIP | 1초마다 오는 status 의 `fw`·`id` (ID 0 이면 경고) |
| 풀업·단락 | 오브 없는 셀의 DQ 가 LOW 로 고정되면 `LOW` 표시 |
| DS2431 접점 | 셀별 인식·CRC 오류·접촉 튐(300ms 안에 끊겼다 복귀) 횟수 |
| DS2431 통신 | Read ROM(0x33)·Read Memory(0xF0) 5회 성공 수 (`M<n>`) |
| LED | 슬롯별 링 점등(`L<n>`), 순차 점등(`W`), 체인별(`A1`/`A2`), 이펙트(`C0~9`) |

LED 는 눈으로 봐야 하므로 체크리스트의 LED 항목은 수동 체크박스다. 페이지
하단의 직접 명령 입력으로 `?`, `S`, `B<밝기>`, `R`(RS485 heartbeat 1회), `Z`
(카운터 초기화)도 보낼 수 있다.
