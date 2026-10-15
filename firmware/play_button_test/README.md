# Play 버튼 단독 테스트

`play_button_test.ino`를 Arduino IDE에서 열어 ESP32S3 Dev Module과 실제 연결 포트를 선택한다.
Adafruit NeoPixel 라이브러리가 필요하다. ESP32 Arduino core 2.0.17 기준.
USB-UART 포트를 쓰면 USB CDC On Boot는 Disabled로 설정한다.
네이티브 USB 포트를 쓰면 USB CDC On Boot를 Enabled로 설정한다.
시리얼 모니터 속도는 115200이다. 업로드하면 기존 ESP32 프로그램은 이 테스트로 교체된다.

## 연결

확인 기준: master_2/Base_Kicad_reviewed_20261008 PCB와 ESP32-S3-DevKitC-1 핀 배열.
실물 단자 표시도 확인하고, 전원을 끈 상태에서 연결한다.

- Master Play 단자 J7 1번 GND -> button PCB J1 1번 GND
- J7 2번 LED DATA -> J1 2번 LED GPIO
- J7 3번 EX_5V -> J1 3번 LED 5V
- J7 4번 BTN -> J1 4번 BTN

단자 좌우 방향이 아닌 핀 번호/신호명 기준으로 연결한다.
Master EX_5V 입력에는 외부 5V와 GND를 공급한다. ESP32 USB만으로는 EX_5V LED 전원이 공급되지 않는다.
ESP32는 USB로 공급하며, 외부 전원과 Master/ESP32/Button의 GND는 공통이다.
테스트를 위해 별도의 외부 3.3V 공급을 추가하지 않는다.

## 동작

- GPIO7 active LOW 입력, GPIO4 WS2812B 데이터 출력, LED 8개.
- 버튼을 누르면 초록색 ON, 떼면 OFF. 밝기 32/255, 디바운스 25ms.
- 버튼 PCB의 병렬 스위치 중 하나만 눌러도 동작한다.
- 시리얼에 PRESSED/RELEASED와 누른 횟수를 출력하고 1초마다 상태를 표시한다.
- PRESSED가 나오는데 LED가 꺼져 있으면 EX_5V 공급과 DATA 배선을 확인한다.
- 업로드 및 실물 동작 확인은 별도로 해야 한다.

핀 배열 참고: https://documentation.espressif.com/esp-dev-kits/en/latest/esp32s3/esp32-s3-devkitc-1/user_guide_v1.0.html
