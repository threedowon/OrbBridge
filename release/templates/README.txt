Voice Orb Demo — 체험용 패키지
==============================

Python 설치 없이 실행할 수 있습니다.


실행 방법
---------

1. OrbBridge.exe 더블클릭

2. 브라우저가 자동으로 열리지 않으면:
   - Screen (멜로디): http://127.0.0.1:8080/screen
   - Table (오브):    http://127.0.0.1:8080/table

3. Screen에서 start 클릭

4. Table에서 오브를 그리드에 올리기


주의
----

- Windows 전용 (.exe)
- 인터넷 연결 불필요 (모두 이 PC 안에서 동작)
- ESP / 시리얼 없음 (가상 table 모드)
- 종료: 검은 콘솔 창에서 Ctrl+C 또는 창 닫기
- 8080, 8765, 9000 포트가 다른 프로그램에 사용 중이면 실행 실패


문제 해결
---------

"Port already in use"
  → 이전 OrbBridge가 아직 실행 중일 수 있습니다.
  → 작업 관리자에서 OrbBridge.exe 종료 후 다시 실행

Screen 우상단 Disconnected
  → OrbBridge.exe가 실행 중인지 확인
  → 페이지 새로고침


데이터 파일 (exe 옆, 첫 실행 후 생성)
--------------------------------------
  config.json
  orb_mapping.json
  stage_data.json
  voice_catalog.json
  sounds/
