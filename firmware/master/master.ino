/*
 * OrbBridge — Master ESP32-S3  (이벤트 방식 — 듣기만)
 *
 * RS485: slave 들의 자율 전송을 듣기만 한다(송신 안 함). → 단방향이라 신호가 깨끗하다.
 *   - 받은 JSON({...})은 PC USB Serial 로 그대로 relay (PC 포맷 무변경)
 * USB Serial → PC (main.py), 115200
 * 버튼: 다시듣기 / 재생 (INPUT_PULLUP)
 *
 * RS485 핀·속도: TX=20, RX=21, 9600 8N1
 *   (master 는 RS485 로 송신하지 않으므로 TX 는 사실상 미사용)
 */

#include <Adafruit_NeoPixel.h>
#include <HardwareSerial.h>

#define TX_PIN       20
#define RX_PIN       21
#define RGB_PIN      48
#define LED_BRIGHTNESS 32
#define RS485_BAUD   9600

#define PIN_BTN_LISTEN 41
#define PIN_BTN_PLAY   42
#define BTN_COOLDOWN   350

Adafruit_NeoPixel led(1, RGB_PIN, NEO_GRB + NEO_KHZ800);
HardwareSerial RS485(1);

String rs485Line;
unsigned long lastListenMs = 0;
unsigned long lastPlayMs = 0;
bool listenWasDown = false;
bool playWasDown = false;

// 논블로킹 LED
unsigned long ledOffAt = 0;

// 진단: master 생존 + relay 카운트
unsigned long lastDebugMs = 0;
unsigned long relayCount = 0;

void pcSendLine(const String& line) {
  Serial.println(line);
}

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
  pollButton(PIN_BTN_LISTEN, listenWasDown, lastListenMs, "listen");
  pollButton(PIN_BTN_PLAY, playWasDown, lastPlayMs, "play");
}

// slave 가 보낸 한 줄 → '{' 부터 잘라 PC 로 relay (앞 잡음 있어도 OK).
void handleLine(const String& line) {
  int b = line.indexOf('{');
  if (b < 0) return;                 // JSON 아님 → 무시
  String json = (b == 0) ? line : line.substring(b);
  pcSendLine(json);
  relayCount++;
  if (json.indexOf("heartbeat") < 0) blinkYellow();  // 실제 이벤트만 노란색
}

void pollRs485() {
  while (RS485.available()) {
    char c = (char)RS485.read();
    if (c == '\r') continue;
    if (c == '\n') {
      handleLine(rs485Line);
      rs485Line = "";
      continue;
    }
    if (rs485Line.length() < 300) rs485Line += c;
  }
}

void setup() {
  Serial.begin(115200);
  delay(1500);

  led.begin();
  led.setBrightness(LED_BRIGHTNESS);
  led.clear();
  led.show();

  pinMode(PIN_BTN_LISTEN, INPUT_PULLUP);
  pinMode(PIN_BTN_PLAY, INPUT_PULLUP);

  RS485.begin(RS485_BAUD, SERIAL_8N1, RX_PIN, TX_PIN);
  rs485Line.reserve(320);

  // 준비 완료: 초록
  led.setPixelColor(0, led.Color(0, 255, 0));
  led.show();
  ledOffAt = 0;
}

void loop() {
  pollRs485();
  pollButtons();
  updateLed();

  if (millis() - lastDebugMs >= 2000) {
    Serial.print("#master alive relay=");
    Serial.println(relayCount);
    lastDebugMs = millis();
  }
  delay(1);
}
