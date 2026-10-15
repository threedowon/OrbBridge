/*
 * OrbBridge — Master ESP32-S3  (이벤트 방식 — 듣기만 + 바이너리 수신)
 *
 * RS485: slave 들의 바이너리 프레임(13B + CRC8)을 받아 relay 한다.
 *   - CRC 검증 후 JSON 으로 변환해 PC USB Serial 로 relay (PC 포맷 무변경)
 *   - 깨진 프레임은 CRC 불일치로 버림 + 다음 STX 로 resync.
 *   - 송신은 LED 이펙트 브로드캐스트("C<n>") 하나뿐이라 DE 는 대부분 수신 상태다.
 * USB Serial → PC (main.py), 115200
 * 버튼: 다시듣기(15) / 재생(7) / 디페스타(16) (INPUT_PULLUP — 핀↔GND, 외부저항 불필요)
 * 홀 센서: 시작(5, 6) — 두 개 모두 자석을 감지하는 순간에만 start 를 한 번 보낸다.
 * 출력: 디페스타 LED 데이터 핀(18) — PC(main.py)가 '정답 완성' 상태를 D1/D0 명령으로 지시.
 *        활성화 시 WS2812B 8개 흰색, 누르거나 비활성화되면 꺼짐.
 * Play/다시듣기 LED: 누르는 동안 흰색, 떼면 꺼짐. 요청은 누름당 한 번.
 *
 * 프레임(13B): [STX=0x02][TYPE][SLAVE][CELL][UID 8B][CRC8]  (heartbeat 는 UID 첫 2B=state)
 * RS485 핀·속도: TX=39, RX=40, DE=42(하드웨어 RTS), 115200 8N1 — slave 와 맞춘다.
 */

#include <Adafruit_NeoPixel.h>
#include <HardwareSerial.h>
#include "LedBroadcast.h"
#include "DebouncedButton.h"
#include "HintCommandParser.h"

#define TX_PIN       39
#define RX_PIN       40
#define DE_PIN       42     // RS485 송수신 전환 — 하드웨어 RTS 가 직접 제어한다
#define RGB_PIN      48
#define LED_BRIGHTNESS 32
#define RS485_BAUD   115200

// Master PCB: 다시듣기 GPIO15, 디페스타 버튼 GPIO16, 디페스타 LED 데이터 GPIO18.
// GPIO17은 다시듣기 LED 데이터 핀이다. 43/44(USB 콘솔)·
// 35~37(옥탈 PSRAM)·strapping 핀(0/3/45/46)도 뺐다.
#define PIN_BTN_LISTEN   15   // 다시듣기 (40 에서 이동)
#define PIN_BTN_PLAY     7    // 재생
#define PIN_BTN_DFESTA   16   // 디페스타 (42 에서 이동)
#define PIN_PLAY_LED     4   // Btn1_LED
#define PIN_LISTEN_LED  17   // Btn2_LED
#define PIN_DFESTA_OUT  18   // Btn3_LED (WS2812B 데이터)
#define BUTTON_LED_COUNT 8
#define BTN_COOLDOWN     350

// Tray: either sensor means present; both sensors mean seated.
#define PIN_HALL_START_A 5
#define PIN_HALL_START_B 6
// 디지털 홀 IC(A3144·US1881 등)는 오픈컬렉터라 자석을 감지하면 LOW 를 낸다.
// 감지 시 HIGH 를 내는 센서를 쓰면 0 으로 바꾼다.
#define HALL_ACTIVE_LOW  1
#define HALL_DEBOUNCE_MS 50   // 감지 경계에서 값이 떨리는 것을 걸러낸다
#define TRAY_REPORT_MS 1000

#define FRAME_LEN 13
#define STX       0x02
#define T_ADDED   1
#define T_REMOVED 2
#define T_HEART   3
#define T_LED_ACK 5
#define T_LED_ACK_TIMING 6

Adafruit_NeoPixel led(1, RGB_PIN, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel playLed(BUTTON_LED_COUNT, PIN_PLAY_LED, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel listenLed(BUTTON_LED_COUNT, PIN_LISTEN_LED, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel dfestaLed(BUTTON_LED_COUNT, PIN_DFESTA_OUT, NEO_GRB + NEO_KHZ800);
bool dfestaLedOn = false;
HardwareSerial RS485(1);

unsigned long lastPlayMs = 0;
unsigned long lastDfestaMs = 0;
unsigned long lastListenMs = 0;
DebouncedButton playButton;
DebouncedButton dfestaButton;
DebouncedButton listenButton;
bool hallPresent = false;
bool hallSeated = false;
bool hallRawA = false;
bool hallRawB = false;
bool hallStateKnown = false;
unsigned long lastTrayReportMs = 0;
unsigned long hallRawChangedMs = 0;  // raw 가 바뀐 시각
unsigned long ledOffAt = 0;

// 진단
unsigned long lastDebugMs = 0;
unsigned long relayCount = 0;   // 유효 프레임 relay 수
unsigned long badCount = 0;     // CRC 불일치(깨진 프레임) 수
unsigned long lastBusRxMs = 0;
LedBroadcast ledBroadcast;
HintCommandParser hintCommand;
bool hintPending = false;
uint32_t hintQueuedAt = 0;

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

void pollButton(int pin, DebouncedButton& button, unsigned long& lastFire,
                const char* action, Adafruit_NeoPixel& strip, bool isDfesta) {
  if (!button.update(digitalRead(pin) == LOW, millis())) return;
  if (isDfesta) {
    // Release does not relight it; only the next PC D1 can activate it.
    if (button.down) setDfestaLed(false);
  } else {
    strip.fill(button.down ? strip.Color(255, 255, 255) : 0);
    strip.show();
  }
  if (button.down) fireGameButton(action, lastFire);
}

void pollButtons() {
  pollButton(PIN_BTN_PLAY, playButton, lastPlayMs, "play", playLed, false);
  pollButton(PIN_BTN_DFESTA, dfestaButton, lastDfestaMs, "dfesta", dfestaLed, true);
  pollButton(PIN_BTN_LISTEN, listenButton, lastListenMs, "listen", listenLed, false);
}

bool hallDetected(int pin) {
  const bool high = digitalRead(pin) == HIGH;
  return HALL_ACTIVE_LOW ? !high : high;
}

void sendTrayState() {
  pcSendLine(String("{\"type\":\"tray\",\"present\":") +
             (hallPresent ? "true" : "false") + ",\"seated\":" +
             (hallSeated ? "true" : "false") + "}");
  lastTrayReportMs = millis();
}

// Report the initial stable state, changes immediately, and a 1 s heartbeat.
// The server owns start decisions; do not also emit a legacy game/start event.
void pollHallStart() {
  const bool a = hallDetected(PIN_HALL_START_A);
  const bool b = hallDetected(PIN_HALL_START_B);
  const unsigned long now = millis();
  if (a != hallRawA || b != hallRawB) {
    hallRawA = a;
    hallRawB = b;
    hallRawChangedMs = now;
  }
  if (now - hallRawChangedMs >= HALL_DEBOUNCE_MS &&
      (!hallStateKnown || hallPresent != (a || b) || hallSeated != (a && b))) {
    hallPresent = a || b;
    hallSeated = a && b;
    hallStateKnown = true;
    sendTrayState();
  } else if (hallStateKnown && now - hallRawChangedMs >= HALL_DEBOUNCE_MS &&
             now - lastTrayReportMs >= TRAY_REPORT_MS) {
    sendTrayState();
  }
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
  } else if (type == T_LED_ACK_TIMING && cell <= 9) {
    uint32_t received = 0;
    for (int i = 0; i < 4; ++i) received |= (uint32_t)f[4 + i] << (8 * i);
    uint16_t processing = (uint16_t)f[8] | ((uint16_t)f[9] << 8);
    uint16_t waiting = (uint16_t)f[10] | ((uint16_t)f[11] << 8);
    uint32_t applied = received + processing;
    pcSendLine(String("{\"type\":\"led_ack\",\"slave\":") + slave +
      ",\"effect\":" + cell + ",\"received_ms\":" + received +
      ",\"applied_ms\":" + applied + ",\"ack_wait_ms\":" + waiting +
      ",\"timing_capped\":" + ((processing == 65535 || waiting == 65535) ? "true" : "false") + "}");
  } else if (type == T_LED_ACK && cell <= 9) {
    uint32_t received = 0, applied = 0;
    for (int i = 0; i < 4; ++i) {
      received |= (uint32_t)f[4 + i] << (8 * i);
      applied |= (uint32_t)f[8 + i] << (8 * i);
    }
    pcSendLine(String("{\"type\":\"led_ack\",\"slave\":") + slave +
      ",\"effect\":" + cell + ",\"received_ms\":" + received +
      ",\"applied_ms\":" + applied + "}");
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
    lastBusRxMs = millis();
    feedByte((uint8_t)RS485.read());
  }
}

void setDfestaLed(bool on) {
  // A repeated activation command must not relight a held button.
  on = on && !dfestaButton.down;
  if (dfestaLedOn == on) return;
  dfestaLedOn = on;
  dfestaLed.fill(on ? dfestaLed.Color(255, 255, 255) : 0);
  dfestaLed.show();
}

// PC 가 USB 로 보낸 명령 처리:
//   "C<n>" — LED 테스트: RS485 로 브로드캐스트(멱등하게 여러 번 반복 전송)
//   "D1"/"D0" — 디페스타 WS2812B 8개 흰색 / 꺼짐 (GPIO18)
String usbLine;
void pollUsbCommand() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n') {
      if (usbLine.length() == 2 && usbLine[0] == 'C' &&
          usbLine[1] >= '0' && usbLine[1] <= '9') {
        ledBroadcast.queue(usbLine[1] - '0', millis());
        hintPending = false;
      } else if (hintCommand.parse(usbLine.c_str())) {
        ledBroadcast.stop();  // Old effect retries must not overwrite this cue.
        hintPending = true;
        hintQueuedAt = millis();
      } else if (usbLine.length() >= 2 && usbLine[0] == 'D') {
        setDfestaLed(usbLine[1] == '1');
      }
      usbLine = "";
    } else if (c != '\r') {
      if (usbLine.length() < 16) usbLine += c;
      else usbLine = "";
    }
  }
}

void pollLedBroadcast() {
  pollRs485();
  unsigned long now = millis();
  if (ledBroadcast.ready(now, lastBusRxMs)) {
    // Complete command in one write; leading newline clears stale text.
    const uint8_t command[] = {'\n', 'C',
      (uint8_t)('0' + ledBroadcast.effect()), '\n'};
    RS485.write(command, sizeof(command));
    RS485.flush(true);  // TX completion only; preserve received bytes.
    ledBroadcast.sent(millis());
  }
  if (ledBroadcast.finished(millis())) {
    Serial.print("#master led C");
    Serial.print(ledBroadcast.effect());
    Serial.print(" sent=");
    Serial.print(ledBroadcast.count());
    Serial.println("/5 (UART TX; slave receipt unconfirmed)");
    ledBroadcast.stop();
  }
}

void pollHintCommand() {
  if (!hintPending) return;
  const uint32_t now = millis();
  const uint32_t age = now - hintQueuedAt;
  // A busy bus must not delay an obsolete note into the following note.
  if (age > 1000) { hintPending = false; return; }
  if (now - lastBusRxMs < 2) return;
  uint16_t remaining = hintCommand.duration > age ? hintCommand.duration - age : 0;
  char line[24];
  if (!remaining) snprintf(line, sizeof(line), "\nH0,0,0\n");
  else snprintf(line, sizeof(line), "\nH%u,%u,%u\n", hintCommand.slave,
                hintCommand.cell, remaining);
  RS485.print(line);
  RS485.flush(true);
  hintPending = false;
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

  pinMode(PIN_HALL_START_A, INPUT_PULLUP);
  pinMode(PIN_HALL_START_B, INPUT_PULLUP);
  hallRawA = hallDetected(PIN_HALL_START_A);
  hallRawB = hallDetected(PIN_HALL_START_B);
  hallRawChangedMs = millis();

  // 버튼 PCB의 LED는 GPIO HIGH/LOW 대신 NeoPixel 데이터로 제어한다.
  Adafruit_NeoPixel* buttonLeds[] = {&playLed, &listenLed, &dfestaLed};
  for (Adafruit_NeoPixel* strip : buttonLeds) {
    strip->begin();
    strip->setBrightness(LED_BRIGHTNESS);
    strip->clear();
    strip->show();
  }
  dfestaLedOn = false;

  RS485.begin(RS485_BAUD, SERIAL_8N1, RX_PIN, TX_PIN);
  // DE 를 하드웨어 RTS 에 맡긴다. 송신 시작·종료에 맞춰 UART 가 직접 토글하므로
  // flush() 뒤에 수동으로 DE 를 내릴 필요가 없다.
  RS485.setPins(RX_PIN, TX_PIN, -1, DE_PIN);
  RS485.setMode(UART_MODE_RS485_HALF_DUPLEX);
  // Our frames are only 13 bytes. Do not rely on core-version defaults or
  // RX-idle interrupts to move short packets from the hardware FIFO.
  bool fifoReady = RS485.setRxFIFOFull(1);
  bool timeoutReady = RS485.setRxTimeout(1);
  Serial.println("#master firmware=rs485-rx-v3");
  Serial.print("#master rx fifo1="); Serial.print(fifoReady ? "ok" : "FAILED");
  Serial.print(" timeout1="); Serial.println(timeoutReady ? "ok" : "FAILED");

  led.setPixelColor(0, led.Color(0, 255, 0));   // 준비 완료: 초록
  led.show();
  ledOffAt = 0;
}

void loop() {
  pollRs485();
  pollUsbCommand();   // PC → RS485 LED 명령 전달
  pollHintCommand();
  pollLedBroadcast();
  pollButtons();
  pollHallStart();
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
