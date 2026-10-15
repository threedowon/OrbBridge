# 홀센서 2개 / Play LED 테스트

Arduino IDE에서 hall_led_test.ino를 열고, 앞서 Play 테스트에 성공한 보드·포트·USB 설정으로 업로드한다.
Adafruit NeoPixel 라이브러리를 사용한다. 시리얼 모니터는 115200.
업로드하면 ESP32에서 실행 중인 Play 테스트 대신 이 테스트가 실행된다.

- 홀센서 1번: GPIO5, 감지 시 LOW.
- 홀센서 2번: GPIO6, 감지 시 LOW.
- LED: 기존 Play 단자에 연결된 버튼 PCB 8개, GPIO4 데이터 출력.
- 1번만 감지: 빨강. 2번만 감지: 파랑. 둘 다 감지: 보라. 미감지: 꺼짐.
- 버튼 누름은 이 테스트의 LED 색상에 영향을 주지 않는다.
- 센서별 30ms 디바운스, LED 밝기 32/255.

LED 배선과 외부 EX_5V 공급은 play_button_test와 동일하게 유지한다.
전원을 끄고 각 센서를 Master의 해당 Hall 단자에 GND/5V/신호 표시대로 연결한다.
모듈마다 핀 순서가 다를 수 있으므로 선 색이나 방향만 보고 연결하지 않는다.
센서 OUT은 Master의 Hall 입력 회로를 통해 연결하며, 5V 출력 신호를 ESP32 GPIO에 직접 연결하지 않는다.

시리얼에는 DETECTED/CLEAR, GPIO 원시 값, LED 색상이 상태 변경 시와 1초마다 출력된다.
실제 모듈이 감지 시 HIGH인 것을 확인한 경우에만 해당 HALL_n_ACTIVE_LOW를 false로 변경한다.
이 설정은 논리 반전만 하며 전압 변환 기능은 없다.
