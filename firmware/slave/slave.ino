/*
 * OrbBridge — Slave ESP32-S3  (이벤트 + 반송파 감지 + 바이너리 프레임)
 *
 * 16개 OneWire 버스에서 DS2431을 읽는다.
 * RS485: slave 가 자율 전송(단방향). master 는 듣기만.
 *   - 전송은 13바이트 바이너리 프레임(+CRC8) — JSON 보다 ~5배 짧아 충돌·깨짐↓, CRC 로 검출.
 *   - 전송 전에 버스를 확인하고 사용 중이면 무작위 시간만큼 대기한다.
 *   - 유실돼도 heartbeat의 점유 마스크로 PC가 자가복구한다.
 *
 * 프레임(13B): [STX=0x02][TYPE][SLAVE][CELL][UID 0..7 (8B)][CRC8]
 *   TYPE 값은 1=added, 2=removed, 3=heartbeat다.
 *   heartbeat는 CELL=0이며 UID 자리 첫 2바이트에 점유 마스크를 저장한다.
 *   CRC8은 crc8(frame[1..11])로 계산한다.
 *
 * RS485 핀·속도: TX=20, RX=21, 9600 8N1
 */

#include <OneWire.h>
#include <Adafruit_NeoPixel.h>
#include <HardwareSerial.h>

#define TX_PIN       20
#define RX_PIN       21
#define RGB_PIN      48
#define LED_BRIGHTNESS 32
#define RS485_BAUD   9600
#define SLAVE_ID     6      // 보드마다 고유하게 (1,2,3…)
#define HEARTBEAT_MS 2000

#define RING_PIN        38
#define LEDS_PER_RING   12
// 임시로 밝기를 낮춰 전류를 줄이며 외부 5V 전원을 연결하면 32로 올린다.
#define RING_BRIGHTNESS 8

#define DEBUG_USB    0

// 반송파 감지 설정
#define BUS_IDLE_MS  4
#define CS_MAX_TRIES 20

// 바이너리 프레임
#define FRAME_LEN 13
#define STX       0x02
#define T_ADDED   1
#define T_REMOVED 2
#define T_HEART   3

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
unsigned long lastBusRxMs = 0;
unsigned long ledOffAt = 0;

// Dallas/Maxim CRC8 (OneWire 와 동일 다항식)
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

void drainBus() {
  while (RS485.available()) {
    RS485.read();
    lastBusRxMs = millis();
  }
}

// 버스가 조용할 때 무작위 대기 후 바이너리 프레임을 전송한다.
void csSendBytes(const uint8_t* buf, int len) {
  for (int t = 0; t < CS_MAX_TRIES; t++) {
    drainBus();
    if (millis() - lastBusRxMs >= BUS_IDLE_MS) {
      unsigned long backoff = 1 + (unsigned long)random(0, 8) + SLAVE_ID;
      unsigned long t0 = millis();
      while (millis() - t0 < backoff) drainBus();
      if (millis() - lastBusRxMs >= BUS_IDLE_MS) {
        RS485.write(buf, len);
        RS485.flush();
        lastBusRxMs = millis();
        return;
      }
    } else {
      delay(1);
    }
  }
  // 계속 바쁨 → 이번 전송 포기(자가복구가 메꿈)
}

void sendFrame(uint8_t type, uint8_t cell, const byte* uid, uint16_t occupancyMask) {
  uint8_t f[FRAME_LEN];
  f[0] = STX;
  f[1] = type;
  f[2] = SLAVE_ID;
  f[3] = cell;
  if (type == T_HEART) {
    f[4] = (uint8_t)(occupancyMask & 0xFF);
    f[5] = (uint8_t)((occupancyMask >> 8) & 0xFF);
    for (int i = 6; i < 12; i++) f[i] = 0;
  } else {
    for (int i = 0; i < 8; i++) f[4 + i] = uid[i];
  }
  f[12] = crc8(&f[1], 11);
  csSendBytes(f, FRAME_LEN);
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

void sendHeartbeat() {
  uint16_t occupancyMask = 0;
  for (int i = 0; i < BUS_COUNT; i++) {
    if (hasLast[i]) occupancyMask |= (uint16_t)(1u << i);
  }
  sendFrame(T_HEART, 0, nullptr, occupancyMask);
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
  setRing(i, ring.Color(255, 147, 41));
  flashRed();
  sendFrame(T_ADDED, i + 1, addr, 0);
}

void onRemoved(int i, const byte addr[8]) {
  setRing(i, 0);
  sendFrame(T_REMOVED, i + 1, addr, 0);
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

  randomSeed(SLAVE_ID * 7919 + 13);
  lastHeartbeatMs = millis() - (unsigned long)SLAVE_ID * 300;
}

void loop() {
  drainBus();

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
