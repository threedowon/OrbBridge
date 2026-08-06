# SineSynth

에이블톤 미설치 PC용 로컬 대체 프로그램이다. loopMIDI 입력을 수신해 포트와 채널을 그룹/멤버로 변환하고, 사인파 재생과 실시간 로그를 제공한다.

## 폴더 배치

```text
<프로젝트 루트>/
  app/app_paths.py
  audioengine/sinesynth.py
  audioengine/sinesynth_settings.json  # 실행 후 저장되는 설정
  data/portmapping.json
```

스크립트는 `app/app_paths.py`의 `data_dir()`를 우선 사용한다.

- 개발 모드: `<repo_root>/data/portmapping.json`
- PyInstaller 빌드: `<exe 위치>/data/portmapping.json`
- `app_paths.py`를 못 찾으면 상위 폴더의 `data/portmapping.json`을 탐색한다.

커스텀 매핑 파일을 사용할 수도 있다.

```bash
python sinesynth.py C:\path\to\portmapping.json
```

## 설치 및 실행

```bash
pip install -r requirements.txt
python audioengine/sinesynth.py
```

## 기능

- 실행 시 `portmapping.json`과 실제 MIDI 입력 포트를 한 번 매칭해 개방
- 고정 열 너비 로그 테이블
  - 시간
  - 포트 인덱스
  - 포트명
  - 채널
  - 멤버명
  - NOTE_ON / NOTE_OFF
  - 원본 노트
  - 마스터 피치 가산값
  - 개별 피치 가산값
  - 최종 피치
  - 벨로시티
- MIDI 36~43 수동 테스트 버튼
  - 버튼 누름: NOTE_ON
  - 버튼 뗌: NOTE_OFF
- 마스터 피치: `-48~+48 st`
- MIDI 36~43 개별 피치: 각 `-12~+12 st`
- 음소거, 볼륨, 로그 지우기
- 설정 저장 및 종료 시 자동 저장

## 설정 파일

설정은 `sinesynth.py`와 같은 폴더의 `sinesynth_settings.json`에 저장된다.

```json
{
  "master_pitch": 0,
  "note_offsets": {
    "36": 0,
    "37": 0,
    "38": 0,
    "39": 0,
    "40": 0,
    "41": 0,
    "42": 0,
    "43": 0
  },
  "volume": 0.4,
  "muted": false
}
```

## 포트 재검색 제거

기존 재검색은 현재 리스너를 중지하고 `mido.get_input_names()`를 다시 호출한 뒤 포트를 재개방했다. 입력 포트 스레드가 완전히 종료되기 전에 같은 포트를 다시 열면 충돌할 수 있어 제거했다. 포트 구성 변경 후에는 프로그램을 재실행한다.

## 참고

loopMIDI는 Windows 전용이므로 동일한 Windows 테스트 PC에서 실행해야 한다.

- 피치 설정을 변경하면 기존 활성 보이스를 즉시 제거한다. 변경 후 새 NOTE_ON부터 새 피치가 적용된다.
- 수동 테스트와 실제 MIDI 입력은 같은 `(그룹, 채널, 원본 노트)` 보이스 키를 사용하므로 같은 음이 이중 생성되지 않는다.
- Windows에서는 SineSynth 중복 실행을 차단한다. 이전 프로세스와 새 프로세스가 동시에 같은 MIDI를 재생하는 상황을 막는다.
