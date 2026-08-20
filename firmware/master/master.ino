/*
 * OrbBridge — Master ESP32-S3  (이벤트 방식 — 듣기만 + 바이너리 수신)
 *
 * RS485: slave 들의 바이너리 프레임(13B + CRC8)을 듣기만 한다(송신 안 함 → 단방향).
 *   - CRC 검증 후 JSON 으로 변환해 PC USB Serial 로 relay (PC 포맷 무변경)
 *   - 깨진 프레임은 CRC 불일치로 버림 + 다음 STX 로 resync.
 * USB Serial → PC (main.py), 115200
 * 버튼: 다시듣기(40) / 시작(41) / 디페스타(42) (INPUT_PULLUP — 핀↔GND, 외부저항 불필요)
 * 출력: 디페스타 LED(39) — PC(main.py)가 '정답 완성' 상태를 D1/D0 명령으로 지시.
 *        정답 유지 중 HIGH, 오답/게임종료 시 LOW.
 *
 * 프레임(13B): [STX=0x02][TYPE][SLAVE][CELL][UID 8B][CRC8]  (heartbeat 는 UID 첫 2B=state)
 * RS485 핀·속도: TX=20, RX=21, 9600 8N1  (master 는 RS485 송신 안 함)
 */

#include <Adafruit_NeoPixel.h>
#include <HardwareSerial.h>

#define TX_PIN       20
#define RX_PIN       21
#define RGB_PIN      48
#define LED_BRIGHTNESS 32
#define RS485_BAUD   9600

#define PIN_BTN_LISTEN   40   // 다시듣기
#define PIN_BTN_PLAY     41   // 재생
#define PIN_BTN_DFESTA   42   // 디페스타
#define PIN_DFESTA_OUT   39   // 디페스타 출력(LED) — PC 명령(D1/D0)으로 점등/소등
// LED 캐소드가 GPIO39 에 물린 액티브 로우 배선이라 LOW 가 점등이다.
#define DFESTA_LED_ON    LOW
#define DFESTA_LED_OFF   HIGH
#define BTN_COOLDOWN     350

#define FRAME_LEN 13
#define STX       0x02
#define T_ADDED   1
#define T_REMOVED 2
#define T_HEART   3

Adafruit_NeoPixel led(1, RGB_PIN, NEO_GRB + NEO_KHZ800);
HardwareSerial RS485(1);

unsigned long lastPlayMs = 0;
unsigned long lastDfestaMs = 0;
unsigned long lastListenMs = 0;
bool playWasDown = false;
bool dfestaWasDown = false;
bool listenWasDown = false;
unsigned long ledOffAt = 0;

// 진단
unsigned long lastDebugMs = 0;
unsigned long relayCount = 0;   // 유효 프레임 relay 수
unsigned long badCount = 0;     // CRC 불일치(깨진 프레임) 수

// 바이너리 프레임 조립 버퍼
uint8_t frame[FRAME_LEN];
int frameLen = 0;

uint8_t crc8(const uint8_t* data, int len) {
  uint8_t crc = 0;
  for (int i = 0; i < len; i++) {
    uint8_t inbyte = data[i];
    for (int j = 0; j < 8; j++) {
      uint8_t mix = (crc ^ inbyte) & 0x01;
      crc >>= 1;
      if (mix) crc ^= 0x8C;
      inbyte >>= 1;
    }
  }
  return crc;
}

void pcSendLine(const String& line) { Serial.println(line); }

void blinkYellow() {
  led.setPixelColor(0, led.Color(255, 255, 0));
  led.show();
  ledOffAt = millis() + 30;
}

void updateLed() {
  if (ledOffAt != 0 && millis() >= ledOffAt) {
    led.clear();
    led.show();
    ledOffAt = 0;
  }
}

void sendGameAction(const char* action) {
  pcSendLine(String("{\"type\":\"game\",\"action\":\"") + action + "\"}");
}

void fireGameButton(const char* action, unsigned long& lastFire) {
  const unsigned long now = millis();
  if (now - lastFire < BTN_COOLDOWN) return;
  sendGameAction(action);
  lastFire = now;
}

void pollButton(int pin, bool& wasDown, unsigned long& lastFire, const char* action) {
  const bool down = digitalRead(pin) == LOW;
  if (down && !wasDown) fireGameButton(action, lastFire);
  wasDown = down;
}

void pollButtons() {
  pollButton(PIN_BTN_PLAY, playWasDown, lastPlayMs, "play");
  pollButton(PIN_BTN_DFESTA, dfestaWasDown, lastDfestaMs, "dfesta");
  pollButton(PIN_BTN_LISTEN, listenWasDown, lastListenMs, "listen");
}

// 유효 프레임 → JSON 변환해 PC 로 relay
void emitFrame(const uint8_t* f) {
  uint8_t type  = f[1];
  uint8_t slave = f[2];
  uint8_t cell  = f[3];
  String json;
  if (type == T_HEART) {
    uint16_t state = (uint16_t)f[4] | ((uint16_t)f[5] << 8);
    json = String("{\"type\":\"heartbeat\",\"slave\":") + slave + ",\"state\":" + state + "}";
    pcSendLine(json);
  } else if (type == T_ADDED || type == T_REMOVED) {
    const char* HX = "0123456789ABCDEF";
    json = String("{\"type\":\"") + (type == T_ADDED ? "added" : "removed") +
           "\",\"slave\":" + slave + ",\"cell\":" + cell + ",\"id\":\"";
    for (int i = 0; i < 8; i++) {
      if (i) json += ':';
      json += HX[(f[4 + i] >> 4) & 0x0F];
      json += HX[f[4 + i] & 0x0F];
    }
    json += "\"}";
    pcSendLine(json);
    blinkYellow();
  } else {
    return;  // 알 수 없는 type
  }
  relayCount++;
}

// 바이너리 스트림 파서 (STX 동기 + CRC 검증 + resync)
void feedByte(uint8_t b) {
  if (frameLen == 0) {
    if (b == STX) { frame[frameLen++] = b; }
    return;   // STX 전 바이트는 버림(잡음)
  }
  frame[frameLen++] = b;
  if (frameLen < FRAME_LEN) return;

  // 13바이트 모임 — CRC 확인
  if (crc8(&frame[1], 11) == frame[12]) {
    emitFrame(frame);
    frameLen = 0;
  } else {
    badCount++;
    // 깨진 프레임 → 버퍼 안에서 다음 STX 를 찾아 그 지점부터 재조립(resync)
    int start = -1;
    for (int i = 1; i < FRAME_LEN; i++) { if (frame[i] == STX) { start = i; break; } }
    if (start < 0) { frameLen = 0; return; }
    int n = FRAME_LEN - start;
    for (int i = 0; i < n; i++) frame[i] = frame[start + i];
    frameLen = n;
  }
}

void pollRs485() {
  while (RS485.available()) {
    feedByte((uint8_t)RS485.read());
  }
}

// PC 가 USB 로 보낸 명령 처리:
//   "C<n>" — LED 테스트: RS485 로 브로드캐스트(멱등하게 여러 번 반복 전송)
//   "D1"/"D0" — 디페스타 LED(GPIO39) HIGH/LOW (정답 완성 표시등)
String usbLine;
void pollUsbCommand() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n') {
      if (usbLine.length() >= 2 && usbLine[0] == 'C') {
        for (int r = 0; r < 5; r++) {
          RS485.print(usbLine);
          RS485.print('\n');
          RS485.flush();
          delay(3);
        }
      } else if (usbLine.length() >= 2 && usbLine[0] == 'D') {
        digitalWrite(PIN_DFESTA_OUT, usbLine[1] == '1' ? DFESTA_LED_ON : DFESTA_LED_OFF);
      }
      usbLine = "";
    } else if (c != '\r') {
      if (usbLine.length() < 16) usbLine += c;
      else usbLine = "";
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(1500);

  led.begin();
  led.setBrightness(LED_BRIGHTNESS);
  led.clear();
  led.show();

  pinMode(PIN_BTN_PLAY, INPUT_PULLUP);
  pinMode(PIN_BTN_DFESTA, INPUT_PULLUP);
  pinMode(PIN_BTN_LISTEN, INPUT_PULLUP);

  pinMode(PIN_DFESTA_OUT, OUTPUT);
  digitalWrite(PIN_DFESTA_OUT, DFESTA_LED_OFF);   // 시작 시 LED 꺼짐

  RS485.begin(RS485_BAUD, SERIAL_8N1, RX_PIN, TX_PIN);

  led.setPixelColor(0, led.Color(0, 255, 0));   // 준비 완료: 초록
  led.show();
  ledOffAt = 0;
}

void loop() {
  pollRs485();
  pollUsbCommand();   // PC → RS485 LED 명령 전달
  pollButtons();
  updateLed();

  if (millis() - lastDebugMs >= 2000) {
    Serial.print("#master alive relay=");
    Serial.print(relayCount);
    Serial.print(" bad=");        // bad=CRC 실패(깨진 프레임) 수 — 신호 품질 지표
    Serial.println(badCount);
    lastDebugMs = millis();
  }
  delay(1);
}
