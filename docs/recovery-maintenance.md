# 복구·정비 기록

M6에서 역할이 있는 운영 파일까지 레거시로 판단해 삭제한 부분을 새 디렉터리
구조에 맞게 복구했다.

## 복구

- `tools/debug/`: 마스터 COM, 슬레이브 하트비트, 셀 점유, 원시 로그 진단
- `audioengine/loopmidi_portsetup.reg`: 현재 MIDI 매핑과 같은 loopMIDI 포트
- `start.bat`, `tools/*.bat`, `tools/debug/debug.bat`, `build/*.bat`
- `data/melodies/Iam.json`, `data/melodies/biplane.json`

## 정비

- `server.bat`과 `server/!run.bat`은 `start.bat` 하나로 통합했다.
- 제거하기로 한 Melody Editor와 Voice Sample Register는 복구하지 않았다.
- Melody Register가 곡별 게임 규칙과 게임 순서를 모두 관리한다.
- `sequence` 모드에서는 성공한 게임 시작마다 다음 멜로디를 선택한다.
- Windows 스크립트의 CRLF를 보장하도록 `.gitattributes`를 복구했다.
- 공연용 `server/inout/midi_out.py`와 `tools/midi_debug.py`를 분리하고 Admin 진단을 연결했다.

