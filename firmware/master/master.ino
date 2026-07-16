/*
 * OrbBridge — Master ESP32-S3  (폴링 방식)
 *
 * RS485 ↔ slave : master가 slave 를 1→N 순서로 하나씩 호출(poll)하고,
 *                 호출받은 slave 만 응답한다. → 버스에 동시에 말하는 놈이
 *                 항상 하나뿐이라 RS485 충돌이 원천적으로 사라진다.
 * USB Serial → PC (main.py) : slave 응답(JSON)을 그대로 relay + 버튼 이벤트
 *
 * 프로토콜 (RS485 위):
 *   master → bus :  "P<id>\n"                              (예: "P3\n")
 *   slave  → bus :  큐에 쌓인 added / removed JSON 줄들 …
 *                   "{\"type\":\"heartbeat\",\"slave\":<id>}"  ← 응답 끝 표시 겸 생존신호
 *
 * PC 쪽(main.py / debug.py)이 보는 줄 포맷은 기존과 동일하다(슬레이브 JSON을
 * 그대로 흘려보냄). heartbeat 는 slave 당 3초에 한 번만 PC 로 relay 해서
 * 로그가 지저분해지지 않게 한다.
 *
 * RS485 핀·속도: TX=20, RX=21, 9600. auto-direction 트랜시버 가정(DE/RE 토글 없음).
 * ── (선택) DE/RE 방향 제어 핀이 따로 있는 하드웨어라면 ──
 *   송신 전 digitalWrite(DE,HIGH), RS485.flush() 후 digitalWrite(DE,LOW) 로 감싸면 된다.
 */

#include <Adafruit_NeoPixel.h>
#include <HardwareSerial.h>

#define TX_PIN       20
#define RX_PIN       21
#define RGB_PIN      48
#define LED_BRIGHTNESS 32   // 255의 1/8
#define RS485_BAUD   9600   // 지연이 크면 slave 와 함께 올려도 됨(예: 57600 → 사이클 ~6배 빨라짐)

#define PIN_BTN_LISTEN 41
#define PIN_BTN_PLAY   42
#define BTN_COOLDOWN   350

// ── 폴링 대상 slave ID 범위 ──────────────────────────────
// 실제 보드 ID 에 맞춰 조정. 없는 ID 는 매 사이클 FIRST_BYTE_TIMEOUT 만큼만 낭비.
#define POLL_ID_MIN  1
#define POLL_ID_MAX  6

// ── 응답 대기 타이밍(ms) ─────────────────────────────────
// FIRST_BYTE: 이 안에 아무 바이트도 안 오면 "slave 부재"로 보고 다음으로.
//   slave 가 OneWire 스캔 중이라 응답이 조금 늦을 수 있어 여유를 둔다.
#define FIRST_BYTE_TIMEOUT_MS  30
// RESPONSE:  이벤트가 아무리 많아도 이 시간이 지나면 강제로 다음 slave 로(안전 상한).
#define RESPONSE_TIMEOUT_MS    200
// PC 로 heartbeat 를 흘려보내는 최소 간격(slave 별). 폴링은 훨씬 자주 돌지만
// PC 로는 이 간격으로만 relay → debug 도구의 online 판정(8초)에 충분.
#define HEARTBEAT_FORWARD_MS   3000

Adafruit_NeoPixel led(1, RGB_PIN, NEO_GRB + NEO_KHZ800);
HardwareSerial RS485(1);

unsigned long lastListenMs = 0;
unsigned long lastPlayMs = 0;
bool listenWasDown = false;
bool playWasDown = false;

// slave 별 마지막으로 PC 에 heartbeat 를 relay 한 시각(스로틀용). 0 = 아직 없음.
unsigned long lastHbFwd[POLL_ID_MAX + 1] = {0};

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

void pollButtons() {
  pollButton(PIN_BTN_LISTEN, listenWasDown, lastListenMs, "listen");
  pollButton(PIN_BTN_PLAY, playWasDown, lastPlayMs, "play");
}

// added / removed 이벤트를 PC 로 relay + 노란색 짧게 깜빡
void relayEvent(const String& line) {
  pcSendLine(line);
  led.setPixelColor(0, led.Color(255, 255, 0));
  led.show();
  delay(12);
  led.clear();
  led.show();
}

// heartbeat 는 slave 당 HEARTBEAT_FORWARD_MS 간격으로만 PC 로 relay
void forwardHeartbeat(int id) {
  static const char* _fmt = "{\"type\":\"heartbeat\",\"slave\":";
  if (id < POLL_ID_MIN || id > POLL_ID_MAX) return;
  const unsigned long now = millis();
  if (lastHbFwd[id] == 0 || now - lastHbFwd[id] >= HEARTBEAT_FORWARD_MS) {
    pcSendLine(String(_fmt) + id + "}");
    lastHbFwd[id] = (now == 0) ? 1 : now;
  }
}

// slave 하나를 호출하고 응답을 받아 PC 로 relay 한다.
// heartbeat 줄(응답 종료 표시)을 받거나 타임아웃이면 리턴.
void pollSlave(int id) {
  // 요청 전송
  RS485.print('P');
  RS485.print(id);
  RS485.print('\n');
  RS485.flush();

  String line;
  line.reserve(96);
  const unsigned long start = millis();
  bool gotAny = false;

  while (true) {
    pollButtons();  // 폴링 대기 중에도 버튼 반응 유지

    if (RS485.available()) {
      char c = (char)RS485.read();
      if (c == '\r') continue;
      if (c == '\n') {
        line.trim();
        if (line.length() > 0) {
          if (line[0] == 'P') {
            // 내 폴 요청 에코(half-duplex) — presence 판정에서 제외, 무시
          } else if (line[0] == '{') {
            gotAny = true;                    // slave 가 실제로 응답함
            if (line.indexOf("heartbeat") >= 0) {
              forwardHeartbeat(id);           // 응답 종료 표시 겸 생존신호
              return;                         // 이 slave 턴 종료
            }
            relayEvent(line);                 // added / removed → 즉시 relay
          }
        }
        line = "";
        continue;
      }
      if (line.length() < 200) line += c;
      continue;
    }

    const unsigned long elapsed = millis() - start;
    if (!gotAny && elapsed > FIRST_BYTE_TIMEOUT_MS) return;  // slave 부재
    if (elapsed > RESPONSE_TIMEOUT_MS) return;               // 안전 상한
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
}

void loop() {
  for (int id = POLL_ID_MIN; id <= POLL_ID_MAX; id++) {
    pollSlave(id);
    pollButtons();
  }
}
