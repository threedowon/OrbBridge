# M6 디렉터리 정리

M6는 공연 런타임, 웹 화면, 등록 도구, 빌드 설정의 물리 경계를 코드 역할과
일치시킨다.

## 기준 구조

```text
app/       공연 중 실행되는 Python 본체
web/       screen·table·admin 정적 UI
tools/     오브·멜로디 등록 도구
data/      모든 실행 JSON
build/     PyInstaller 설정
tests/     자동 검증
```

Python 코드는 `app.*`, `tools.*` 패키지 import를 사용한다. 실행 명령도 파일
경로 직접 실행 대신 `python -m app.launcher`와 `python -m tools.{tool}`로
통일한다.

## 제거한 레거시

- 실제 HTTP 서버가 된 뒤에도 남아 있던 `mock` 설정명과 `mock_server.py`
- `config.json`과 값이 충돌하던 `config.table.json`
- Admin 상태 화면과 기능이 겹치던 독립 `debug/` 서버
- 전체 Melody Register와 기능이 겹치던 간이 Melody Editor
- 삭제된 `sounds/`를 다시 만들던 경로와 Voice Sample Register
- 사용되지 않는 `start_mock_server` 호환 별칭
- 실행할 때마다 의존성을 설치하던 `start.command` 동작

## 유지한 기능

- `READY`, `RUNNING`, `RESETTING` 상태 계약
- Serial·HTTP 입력의 `GameRuntime` 단일 writer
- Python Scheduler·MIDI·WebSocket snapshot
- Orb Register와 전체 Melody Register
- `audio_output: web`의 oscillator 기반 개발 fallback
- PyInstaller용 data seed 복사

## 완료 기준

- `server/`, 루트 `screen/`, `table/`, `admin/` 경로가 남지 않는다.
- `config.table.json`, `debug/`, `sounds/` 및 관련 코드가 남지 않는다.
- 모든 Python import와 정적 파일 경로가 새 구조를 사용한다.
- 세 웹 화면, 게임 상태, Scheduler, 재접속 snapshot 회귀 테스트가 통과한다.
- README·M1–M5 문서가 새 경로를 가리킨다.

