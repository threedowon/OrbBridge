/*
 * OrbBridge — Master ESP32-S3  (폴링 방식)
 *
 * RS485: slave 를 P<id> 로 순번 호출(poll). 호출받은 slave 만 응답 → 충돌 원천 차단.
 *   - slave 응답 JSON({...})은 PC USB Serial 로 그대로 relay (PC 포맷 무변경)
 *   - 응답 끝은 터미네이터(0x04)로 감지. 무응답 slave 는 타임아웃 후 다음으로.
 * USB Serial → PC (main.py), 115200
 * 버튼: 다시듣기 / 재생 (INPUT_PULLUP)
 *
 * RS485 핀·속도: TX=20, RX=21, 38400 8N1 (모듈 한계로 낮춤)
 */

#include <Adafruit_NeoPixel.h>
#include <HardwareSerial.h>

#define TX_PIN       20
#define RX_PIN       21
#define RGB_PIN      48
#define LED_BRIGHTNESS 32   // 255의 1/8
// 저가형 auto-direction 트랜시버가 송신 시작/끝 바이트를 갉아먹어 고속에서 깨진다.
// 폴링이라 충돌은 baud 와 무관하게 0 → 깨끗해질 때까지 내려도 안전(지연만 소폭↑).
#define RS485_BAUD   9600

#define PIN_BTN_LISTEN 41
#define PIN_BTN_PLAY   42
#define BTN_COOLDOWN   350

// 순번 폴링할 slave ID 목록 — 실제 보드들의 SLAVE_ID 와 일치시킬 것.
// 존재하지 않는 ID 는 응답이 없어 POLL_IDLE_MS 만큼 대기 후 넘어간다(치명적 아님).
const uint8_t POLL_IDS[] = {1, 2, 3, 4, 5, 6};
const int NUM_POLL = sizeof(POLL_IDS) / sizeof(POLL_IDS[0]);

const unsigned long POLL_MAX_MS  = 40;  // 한 slave 응답을 기다리는 최대 시간(안전 상한)
const unsigned long POLL_IDLE_MS = 6;   // 이 시간 동안 수신 없으면 응답 종료/부재로 판단

Adafruit_NeoPixel led(1, RGB_PIN, NEO_GRB + NEO_KHZ800);
HardwareSerial RS485(1);

int pollIdx = 0;
unsigned long lastListenMs = 0;
unsigned long lastPlayMs = 0;
bool listenWasDown = false;
bool playWasDown = false;

// 논블로킹 LED (delay 로 폴링 주기를 늦추지 않도록)
unsigned long ledOffAt = 0;

// 진단: master 가 살아서 폴링 중인지 + slave 응답을 몇 개 relay 했는지
unsigned long lastDebugMs = 0;
unsigned long relayCount = 0;
// slave 가 "뭐라도" 보냈는지 (폴 에코가 아닌 바이트 수). sb>0 이면 slave 응답은 오는데
// 깨져서 파싱 실패, sb=0 이면 slave 가 아예 무응답(폴 미도달/단방향 배선).
unsigned long slaveBytes = 0;

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
  if (down && !wasDown) {
    fireGameButton(action, lastFire);
  }
  wasDown = down;
}

void pollButtons() {
  pollButton(PIN_BTN_LISTEN, listenWasDown, lastListenMs, "listen");
  pollButton(PIN_BTN_PLAY, playWasDown, lastPlayMs, "play");
}

// slave 가 보낸 한 줄 처리:
//   '{' 로 시작  → 이벤트/heartbeat → PC 로 relay (+ 이벤트면 LED)
//   그 외        → 자기 폴 명령 에코("P6") 등 → 무시
//   (터미네이터 0x04 는 pollSlave 에서 별도로 응답 종료 판정)
void handleSlaveLine(const String& line) {
  // 앞에 잡음(반이중 에코·워밍업 바이트)이 붙어도 '{' 부터 잘라서 relay
  int b = line.indexOf('{');
  if (b < 0) return;                 // '{' 없음(폴 에코 등) → 무시
  String json = (b == 0) ? line : line.substring(b);
  pcSendLine(json);
  relayCount++;
  if (json.indexOf("heartbeat") < 0) {
    blinkYellow();   // 실제 orb 이벤트만 노란색 (heartbeat 는 조용히)
  }
}

// 한 slave 를 폴링하고 응답을 relay. 응답 완료(터미네이터) 또는 타임아웃까지.
void pollSlave(uint8_t id) {
  // 워밍업 '\r' 2개: auto-direction 이 전송 첫 바이트를 갉아먹어도 'P' 를 보존.
  // '\r' 는 양쪽 파서가 무시하므로, 살아남아도 무해.
  RS485.write('\r');
  RS485.write('\r');
  RS485.print('P');
  RS485.print((int)id);   // 숫자로 출력 ("P6")
  RS485.print('\n');
  RS485.flush();
  delayMicroseconds(150);   // auto-direction 트랜시버 방향 전환 여유

  unsigned long start = millis();
  unsigned long lastByte = millis();
  String line = "";
  bool done = false;

  while (!done && (millis() - start) < POLL_MAX_MS) {
    while (RS485.available()) {
      char c = (char)RS485.read();
      lastByte = millis();
      // 폴 에코("\r\rP6\n")에 없는 바이트 = slave 가 실제로 보낸 것 → 진단 카운트
      if (c != '\r' && c != '\n' && c != 'P' && !(c >= '0' && c <= '9')) {
        slaveBytes++;
      }
      if (c == '\n') {
        if (line.length() == 1 && line[0] == (char)0x04) {
          done = true;              // 터미네이터 → 이 slave 응답 완료
        } else {
          handleSlaveLine(line);    // 이벤트/heartbeat relay, 폴 에코는 무시
        }
        line = "";
      } else if (c != '\r') {
        if (line.length() < 300) line += c;
      }
    }
    pollButtons();                  // 폴 대기 중에도 버튼 반응 유지
    updateLed();
    if (!done && (millis() - lastByte) > POLL_IDLE_MS) {
      break;                        // 무응답 → slave 부재/사망, 다음으로
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

  pinMode(PIN_BTN_LISTEN, INPUT_PULLUP);
  pinMode(PIN_BTN_PLAY, INPUT_PULLUP);

  RS485.begin(RS485_BAUD, SERIAL_8N1, RX_PIN, TX_PIN);

  // 준비 완료: 초록
  led.setPixelColor(0, led.Color(0, 255, 0));
  led.show();
  ledOffAt = 0;
}

void loop() {
  pollButtons();
  pollSlave(POLL_IDS[pollIdx]);
  pollIdx = (pollIdx + 1) % NUM_POLL;
  updateLed();

  // 진단: 2초마다 PC 로 생존+relay 카운트 (main.py 가 "[master] ..." 로 로그).
  // relay=0 이 계속이면 slave 응답을 하나도 못 받는다는 뜻(→ slave/RS485 문제).
  if (millis() - lastDebugMs >= 2000) {
    Serial.print("#master alive relay=");
    Serial.print(relayCount);
    Serial.print(" sb=");           // sb=slave가 보낸 바이트(에코 제외). sb=0=무응답, sb>0=응답이 깨짐
    Serial.println(slaveBytes);
    lastDebugMs = millis();
  }
}
