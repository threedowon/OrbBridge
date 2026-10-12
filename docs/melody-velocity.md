# 멜로디 velocity 편곡 — 2026-10-01

이 값은 **원곡 음원 청취·파형 분석으로 추출한 값이 아니다.** 웹 곡 정보·공식 발매 자료를 확인하고, 저장된 12스텝의 음형과 길이를 바탕으로 작성한 수동 편곡 초안이다. 원곡의 정확한 소절 위치·보컬 강세 일치는 검증하지 못했다. 아래 강약 설명은 이 편곡의 의도이며 원곡에 대한 측정 결과가 아니다.

- 사용 범위 70–127, 대부분 90–115. 모든 곡에 최솟값·최댓값을 강제하지 않는다.
- 음별 고정값이며 재생 시 랜덤 velocity나 타이밍 변경은 없다.
- 31개 일반 곡의 유음 velocity만 변경했다. 테스트 2개, REST 값, 음높이, duration, BPM, 화음, metadata는 보존했다.
- Listen·Play·DFesta의 기존 step.velocity 전달 경로를 그대로 사용한다. 합창의 모든 파트는 해당 스텝과 같은 velocity를 받는다.
- 실제 음량 차이는 재생 악기의 velocity 반응에 따라 달라지므로 현장 청취 조정은 남아 있다.

| 파일 | 유음 범위 | 편곡 의도 |
|---|---|---|
| BTS-BOY_WITH_LUV.json | 88–118 | 짧은 음은 여리게, 뒤따르는 음과 상행 정점 강조 |
| BTS-DYNAMITE.json | 84–116 | 짧은 도입 뒤 도약 강조, 하행 연결음 완화 |
| BTS-PERMISSION_TO_DANCE.json | 87–116 | 긴 음 강조·짧은 응답 약화, 하행하며 완화 |
| CORTIS-JOYRIDE.json | 91–119 | 옥타브 도약 강조, 반복 저음 강약 교대 |
| CORTIS-REDRED.json | 91–120 | 짧은 반복 묶음의 시작과 긴 음 강조 |
| CORTIS-WHAT_YOU_WANT.json | 86–117 | 동음 리듬에 악센트, 짧은 연결음과 끝의 응답 완화 |
| ENHYPEN-BITE_ME.json | 86–116 | 도약과 긴 C5 강조, 연결음 완화, 반복 구절 균형 |
| ENHYPEN-FEVER.json | 78–114 | 여린 시작에서 반복 상행 정점까지 점진적으로 증가 |
| ENHYPEN-POLAROID_LOVE.json | 84–104 | 좁고 부드러운 강약, 하행과 종지 완화 |
| GD-Crooked.json | 102–123 | 반복 두 음에 악센트 교대, 중간 정점 강조 |
| GD-HEARTBREAKER.json | 88–119 | 짧은 연결음 약화, 동음 반복에 변화, G4 정점 강조 |
| GD-UNTITLED.json | 70–108 | 여린 시작에서 A4까지 증가한 뒤 종지로 감소 |
| IVE-AFTER_LIKE.json | 86–117 | 상행·재진입 강조, 짧은 연결음과 종지 완화 |
| IVE-I_AM.json | 91–121 | 높은 도입 강조, 중간 상행 재강조, 반복 종지 완화 |
| IVE-REBEL_HEART.json | 95–118 | 반복 구절을 유사하게 유지하며 두 번째 구절 소폭 강화 |
| Iam.json | 91–121 | IVE-I_AM과 동일한 음별 강약 |
| RIIZE-BOOM_BOOM_BASS.json | 85–115 | 긴 음과 리듬 악센트 강조, 짧은 연결음 약화 |
| RIIZE-GET_A_GUITAR.json | 78–116 | 하행 장식음 완화, 쉼 뒤 반복음 악센트 |
| RIIZE-IMPOSSIBLE.json | 90–120 | 짧은 연결음 완화, 상행 정점 강조, 종지 완화 |
| SKZ-GOD_S_MENU.json | 101–127 | 구절 시작과 C5 정점에 강한 악센트, 연결음 대비 |
| SKZ-HELLEVATOR.json | 88–123 | 낮은 도입에서 상행 정점으로 단계적 증가 |
| SKZ-LALALA.json | 102–125 | 반복 리듬에 일정한 악센트, G4 정점 강조 |
| SKZ-MIROH.json | 102–124 | 상행 정점으로 증가, 반복 후반은 강약 교대 |
| TXT-0X1_LOVESONG.json | 94–123 | 도약과 긴 음 강조, 짧은 연결음 약화 |
| TXT-BEAUTIFUL_STRANGERS.json | 91–123 | 긴 A4와 C5 도약 강조, 중간 이완 뒤 마지막 구절 증가 |
| TXT-DEJA_VU.json | 84–118 | 상행과 구절 재진입 강조, 짧은 반복 연결음 완화 |
| aespa-NEXT_LEVEL.json | 98–119 | 반복 A에서 강약 대비, C5 정점과 두 번째 구절 시작 강조 |
| aespa-SAVAGE.json | 96–123 | 상행 정점 강조, 반복 저음은 번갈아 약화, 종지 완화 |
| aespa-SUPERNOVA.json | 102–121 | 각 두 음 중 두 번째를 강조, 쉼 뒤 같은 패턴 유지 |
| biplane.json | 80–99 | 동요의 구절 첫 음 강조, 반복음과 종지 완화 |
| dynamite.json | 84–116 | BTS-DYNAMITE와 동일한 음별 강약 |

## 웹 참고 자료

공식 영상 링크는 곡 식별용으로 확인했다. 링크 발견이 청취를 의미하지 않으며, 자료가 개별 velocity 수치를 뒷받침하지 않는다.

- [aespa Next Level 공식 MV](https://www.youtube.com/watch?v=4TWR90KJl84)
- [aespa Savage 공식 MV](https://www.youtube.com/watch?v=WPdWvnAAurg)
- [aespa Supernova 공식 MV](https://weverse.io/aespa/media/4-163447105)
- [BTS Dynamite 공식 소개](https://ibighit.com/legacy/bts/dynamite-en.html)
- [BTS Permission to Dance 공식 MV](https://www.youtube.com/watch?v=CuklIb9d3fI)
- [CORTIS What You Want·JoyRide 공식 앨범 소개](https://ibighit.com/legacy/cortis/color-outside-the-lines-en.html)
- [CORTIS REDRED 공식 MV](https://www.youtube.com/watch?v=U6BDbXIah-Y)
- [ENHYPEN Polaroid Love 소개](https://magazine.weverse.io/article/view/339?artist=ENHYPEN)
- [ENHYPEN FEVER 공식 MV](https://weverse.io/enhypen/media/2-105465082)
- [ENHYPEN Bite Me 관련 소개](https://magazine.weverse.io/article/view/734?lang=e)
- [G-DRAGON Crooked 공식 MV 안내](https://yg-life.com/archives/21643?lang=en)
- [G-DRAGON Heartbreaker 공식 MV](https://www.youtube.com/watch?v=LOXEVd-Z7NE)
- [G-DRAGON Untitled, 2014 공식 음원](https://www.youtube.com/watch?v=Nged3LwJsvQ)
- [IVE I AM·After LIKE 공식 영상 목록](https://ive-official.jp/mob/news/diarKiji.php?cd=VIDEO&ima=1829&page=2&rw=9&site=DIVE&year=2022)
- [IVE REBEL HEART 공식 소개](https://newsroom.kakaoent.com/news/ives-heroic-return-from-self-love-to-empathy-ive-deepens-message-to-forge-connections-across-all-generations/)
- [RIIZE Get A Guitar 공식 소개](https://riizeofficial.jp/discography/get-a-guitar/)
- [RIIZE Boom Boom Bass 공식 MV 안내](https://riizeofficial.jp/news/2024061702/)
- [Stray Kids God's Menu 공식 MV](https://www.youtube.com/watch?v=TQTlCHxyuu8)
- [Stray Kids LALALALA 공식 앨범 소개](https://straykids.jype.com/discography/8b5tq25d?AmSeq=undefined&PgIndex=undefined)
- [TXT 0X1=LOVESONG 공식 앨범 소개](https://txt.ibighit.com/eng/discography/detail/the_chaos_chapter_freeze.html)
- [TXT Beautiful Strangers 공식 앨범 소개](https://ibighit.com/legacy/txt/together-en.html)
- [TXT Deja Vu 공식 앨범 소개](https://txt.ibighit.com/eng/discography/detail/minisode-3/)
