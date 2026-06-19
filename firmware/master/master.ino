/*
 * OrbBridge — Master ESP32-S3
 *
 * RS485 ← slave (added / removed JSON)
 * USB Serial → PC (main.py)
 * 버튼: 다시듣기 / 재생 (INPUT_PULLUP, 외부 저항 불필요)
 *
 * RS485 핀·속도: 실물 테스트와 동일 (TX=20, RX=21, 9600)
 */

#include <Adafruit_NeoPixel.h>
#include <HardwareSerial.h>

#define TX_PIN       20
#define RX_PIN       21
#define RGB_PIN      48
#define LED_BRIGHTNESS 32   // 255의 1/8
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

void pcSendLine(const String& line) {
  Serial.println(line);
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
  if (down && !wasDown) {
    fireGameButton(action, lastFire);
  }
  wasDown = down;
}

void forwardSlaveLine(const String& line) {
  if (!line.startsWith("{")) return;
  pcSendLine(line);
  // slave JSON 수신 시 잠깐 노란색
  led.setPixelColor(0, led.Color(255, 255, 0));
  led.show();
  delay(30);
  led.clear();
  led.show();
}

void pollRs485() {
  while (RS485.available()) {
    char c = (char)RS485.read();
    if (c == '\r') continue;
    if (c == '\n') {
      rs485Line.trim();
      if (rs485Line.length() > 0) {
        forwardSlaveLine(rs485Line);
      }
      rs485Line = "";
      continue;
    }
    if (rs485Line.length() < 256) {
      rs485Line += c;
    }
  }
}

void pollButtons() {
  pollButton(PIN_BTN_LISTEN, listenWasDown, lastListenMs, "listen");
  pollButton(PIN_BTN_PLAY, playWasDown, lastPlayMs, "play");
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
  rs485Line.reserve(256);

  // 준비 완료: 초록
  led.setPixelColor(0, led.Color(0, 255, 0));
  led.show();
}

void loop() {
  pollRs485();
  pollButtons();
  delay(2);
}
