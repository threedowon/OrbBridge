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
