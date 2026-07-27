/*
 * OrbBridge — Slave ESP32-S3  (이벤트 방식 + Carrier-Sense)
 *
 * 16× OneWire → DS2431
 * RS485: slave 가 자율 전송(단방향). master 는 듣기만 → 신호 깨끗(단방향은 9600에서 검증됨).
 *   - 충돌 방지: 전송 전에 버스가 조용한지 듣고(carrier-sense), 사용 중이면 랜덤 백오프.
 *   - 그래도 가끔 충돌해 유실돼도, heartbeat 의 state 마스크로 PC 가 자가복구.
 * NeoPixel 링 16개 (각 12 LED) → GPIO 38 체인, DS2431 1개당 링 1개
 *
 * RS485 핀·속도: TX=20, RX=21, 9600 8N1
 */

#include <OneWire.h>
#include <Adafruit_NeoPixel.h>
#include <HardwareSerial.h>

#define TX_PIN       20
#define RX_PIN       21
#define RGB_PIN      48
#define LED_BRIGHTNESS 32   // 255의 1/8
#define RS485_BAUD   9600
#define SLAVE_ID     6      // 보드마다 고유하게 (1,2,3…)
#define HEARTBEAT_MS 2000   // 상태(state 포함) 전송 주기 — 자가복구·online 판정용

// NeoPixel 링: DS2431 버스 1개당 12 LED 링 1개, GPIO 38 한 데이터선에 체인
#define RING_PIN        38
#define LEDS_PER_RING   12
// 임시 stopgap: 밝기를 낮춰 전류 감소(브라운아웃 완화). 외부 5V 전원 달면 32로 올려도 됨.
// 192 LED 를 ESP 5V 핀에서 끌면 ~2A 필요 → ESP 공급(~0.5A) 초과 → 보드 전압붕괴.
#define RING_BRIGHTNESS 8

#define DEBUG_USB    0   // 1: USB Serial Monitor에도 상태 출력

// ── Carrier-sense 파라미터 ───────────────────────────────
#define BUS_IDLE_MS  4    // 이만큼 무신호면 버스 비었다고 판단
#define CS_MAX_TRIES 20   // 버스 확보 재시도 횟수 (실패 시 이번 전송 포기 → 자가복구에 위임)

Adafruit_NeoPixel led(1, RGB_PIN, NEO_GRB + NEO_KHZ800);
HardwareSerial RS485(1);

const int BUS_COUNT = 16;
OneWire buses[BUS_COUNT] = {
  OneWire(4), OneWire(5), OneWire(6), OneWire(7),
  OneWire(15), OneWire(16), OneWire(17), OneWire(18),
  OneWire(8), OneWire(3), OneWire(9), OneWire(10),
  OneWire(11), OneWire(12), OneWire(13), OneWire(14)
};

Adafruit_NeoPixel ring(BUS_COUNT * LEDS_PER_RING, RING_PIN, NEO_GRB + NEO_KHZ800);

byte lastAddr[BUS_COUNT][8];
bool hasLast[BUS_COUNT] = {false};
unsigned long lastHeartbeatMs = 0;

// 버스 활동(carrier-sense): RS485 로 마지막으로 바이트를 본 시각
unsigned long lastBusRxMs = 0;

// 온보드 RGB 논블로킹 플래시
unsigned long ledOffAt = 0;

// ── 버스 감지 / carrier-sense 전송 ───────────────────────
// 들어오는 바이트를 비우며 버스 활동 시각을 갱신한다(수신 데이터는 master 만 쓰므로 버림).
void drainBus() {
  while (RS485.available()) {
    RS485.read();
    lastBusRxMs = millis();
  }
}

// 버스가 조용할 때까지 기다렸다가 한 줄 전송(carrier-sense + 랜덤 백오프).
// master 는 듣기만 하므로 버스의 다른 신호는 곧 다른 slave 의 전송이다.
void csSend(const String& line) {
  for (int t = 0; t < CS_MAX_TRIES; t++) {
    drainBus();
    if (millis() - lastBusRxMs >= BUS_IDLE_MS) {
      // 동시 전송 회피: ID + 랜덤 만큼 더 듣고 한 번 더 확인
      unsigned long backoff = 1 + (unsigned long)random(0, 8) + SLAVE_ID;
      unsigned long t0 = millis();
      while (millis() - t0 < backoff) drainBus();
      if (millis() - lastBusRxMs >= BUS_IDLE_MS) {
        RS485.println(line);
        RS485.flush();
        lastBusRxMs = millis();   // 내 전송도 버스 점유
        return;
      }
      // 백오프 중 누가 말하기 시작 → 다시 대기
    } else {
      delay(1);   // 버스 사용 중 → 잠깐 대기
    }
  }
  // 계속 바쁨 → 이번 전송 포기. heartbeat 의 state 마스크로 PC 가 복구.
}

void flashRed() {
  led.setPixelColor(0, led.Color(255, 0, 0));
  led.show();
  ledOffAt = millis() + 80;
}

void updateLed() {
  if (ledOffAt != 0 && millis() >= ledOffAt) {
    led.clear();
    led.show();
    ledOffAt = 0;
  }
}

void setRing(int busIndex, uint32_t color) {
  int base = busIndex * LEDS_PER_RING;
  for (int j = 0; j < LEDS_PER_RING; j++) ring.setPixelColor(base + j, color);
  ring.show();
}

bool sameAddr(const byte a[8], const byte b[8]) {
  for (int i = 0; i < 8; i++) if (a[i] != b[i]) return false;
  return true;
}

void appendAddr(String& out, const byte addr[8]) {
  char hex[] = "0123456789ABCDEF";
  for (int i = 0; i < 8; i++) {
    if (i > 0) out += ':';
    out += hex[(addr[i] >> 4) & 0x0F];
    out += hex[addr[i] & 0x0F];
  }
}

void sendEvent(const char* type, int busIndex, const byte addr[8]) {
  String msg = "{\"type\":\"";
  msg += type;
  msg += "\",\"slave\":";
  msg += SLAVE_ID;
  msg += ",\"cell\":";
  msg += busIndex + 1;
  msg += ",\"id\":\"";
  appendAddr(msg, addr);
  msg += "\"}";
  csSend(msg);
}

// heartbeat: "state" = 16칸 점유 비트마스크(bit i = cell i+1). PC 가 유실 이벤트를 자가복구.
void sendHeartbeat() {
  uint16_t state = 0;
  for (int i = 0; i < BUS_COUNT; i++) if (hasLast[i]) state |= (uint16_t)(1u << i);
  String msg = "{\"type\":\"heartbeat\",\"slave\":";
  msg += SLAVE_ID;
  msg += ",\"state\":";
  msg += state;
  msg += "}";
  csSend(msg);
}

bool readBus(int i, byte addr[8]) {
  buses[i].reset_search();
  if (!buses[i].search(addr)) return false;
  if (OneWire::crc8(addr, 7) != addr[7]) {
#if DEBUG_USB
    Serial.print("CRC error cell "); Serial.println(i + 1);
#endif
    return false;
  }
  return true;
}

void onAdded(int i, const byte addr[8]) {
  setRing(i, ring.Color(255, 147, 41));   // 즉시 시각 피드백(로컬)
  flashRed();
  sendEvent("added", i, addr);            // carrier-sense 전송
}

void onRemoved(int i, const byte addr[8]) {
  setRing(i, 0);
  sendEvent("removed", i, addr);
}

void scanBus(int i) {
  byte addr[8];
  bool present = readBus(i, addr);
  if (present) {
    if (!hasLast[i]) {
      memcpy(lastAddr[i], addr, 8);
      hasLast[i] = true;
      onAdded(i, addr);
    } else if (!sameAddr(lastAddr[i], addr)) {
      onRemoved(i, lastAddr[i]);
      memcpy(lastAddr[i], addr, 8);
      onAdded(i, addr);
    }
  } else {
    if (hasLast[i]) {
      onRemoved(i, lastAddr[i]);
      hasLast[i] = false;
      memset(lastAddr[i], 0, 8);
    }
  }
}

void setup() {
#if DEBUG_USB
  Serial.begin(115200);
  delay(500);
#endif

  led.begin();
  led.setBrightness(LED_BRIGHTNESS);
  led.clear();
  led.show();

  ring.begin();
  ring.setBrightness(RING_BRIGHTNESS);
  ring.clear();
  ring.show();

  RS485.begin(RS485_BAUD, SERIAL_8N1, RX_PIN, TX_PIN);

  randomSeed(SLAVE_ID * 7919 + 13);                 // 보드마다 백오프 시퀀스 다르게
  lastHeartbeatMs = millis() - (unsigned long)SLAVE_ID * 300;  // 초기 위상 어긋나게
}

void loop() {
  drainBus();   // 항상 버스 활동 감지

  for (int i = 0; i < BUS_COUNT; i++) {
    scanBus(i);
    drainBus();
    updateLed();
  }

  if (millis() - lastHeartbeatMs >= HEARTBEAT_MS) {
    sendHeartbeat();
    lastHeartbeatMs = millis();
  }

  updateLed();
  delay(1);
}
