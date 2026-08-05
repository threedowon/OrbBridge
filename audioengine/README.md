# Audio Engine

Ableton과 loopMIDI 관련 운영 파일을 보관한다.

## loopMIDI 포트 복구

1. loopMIDI를 종료한다.
2. `loopmidi_portsetup.reg`를 더블클릭해 레지스트리에 병합한다.
3. loopMIDI를 다시 실행한다.

생성 대상은 `data/portmapping.json`과 같은 `aespa`, `bts`, `cortis`,
`enhypen`, `ive`, `riize`, `skz`, `txt`, `listen` 포트이다. 원본 설정에 있던
`event` 포트도 호환을 위해 보존했으며 현재 Python 런타임은 이 포트를 열지
않는다.
