/*
 * OrbBridge — Slave ESP32-S3  (이벤트 + Carrier-Sense + 바이너리 프레임)
 *
 * 16× OneWire → DS2431
 * RS485: slave 가 자율 전송(단방향). master 는 듣기만.
 *   - 전송은 13바이트 바이너리 프레임(+CRC8) — JSON 보다 ~5배 짧아 충돌·깨짐↓, CRC 로 검출.
 *   - 전송 전 버스가 조용한지 듣고(carrier-sense) 사용 중이면 랜덤 백오프.
 *   - 유실돼도 heartbeat 의 state 마스크로 PC 가 자가복구.
 *
 * 프레임(13B): [STX=0x02][TYPE][SLAVE][CELL][UID 0..7 (8B)][CRC8]
 *   TYPE 1=added 2=removed 3=heartbeat
 *   heartbeat 는 CELL=0, UID 자리 첫 2바이트에 state(비트마스크) 저장
 *   CRC8 = crc8(frame[1..11])
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

// LED 데이터선 2개. 슬롯별 LED 위치는 아래 LED_MAP 으로 배선에 맞게 직접 지정한다.
#define RING_PIN_LOW    39     // LED 스트립 A (지금 슬롯 5~8 연결)
#define RING_PIN_HIGH   38     // LED 스트립 B
#define LEDS_PER_RING   10     // 슬롯당 LED 수
#define LED_COUNT_LOW   40     // 스트립 A 총 LED 수 (지금 슬롯 5~8 = 4×10)
#define LED_COUNT_HIGH  40     // 스트립 B 총 LED 수 (실제 연결 수에 맞게)
// 임시 stopgap: 밝기 낮춰 전류↓. 외부 5V 전원 달면 32~50 으로.
#define RING_BRIGHTNESS 8

#define DEBUG_USB    0

// Carrier-sense
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

Adafruit_NeoPixel ringLow (LED_COUNT_LOW,  RING_PIN_LOW,  NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel ringHigh(LED_COUNT_HIGH, RING_PIN_HIGH, NEO_GRB + NEO_KHZ800);

// ── 슬롯(버스) → LED 위치 표 ──  배선이 바뀌면 여기만 고친다.
//   strip: 0=스트립A(RING_PIN_LOW), 1=스트립B(RING_PIN_HIGH), -1=LED 없음
//   offset: 그 스트립에서 이 슬롯의 첫 LED 인덱스 (슬롯당 LEDS_PER_RING 개)
// 인덱스 = 버스 index (0=슬롯1 … 15=슬롯16)
struct LedMap { int8_t strip; int16_t offset; };
const LedMap LED_MAP[BUS_COUNT] = {
  {-1,  0}, {-1,  0}, {-1,  0}, {-1,  0},   // 슬롯 1~4  : LED 없음
  { 0,  0}, { 0, 10}, { 0, 20}, { 0, 30},   // 슬롯 5~8  : 스트립A(GPIO39) 0/10/20/30
  {-1,  0}, {-1,  0}, {-1,  0}, {-1,  0},   // 슬롯 9~12 : LED 없음
  {-1,  0}, {-1,  0}, {-1,  0}, {-1,  0},   // 슬롯 13~16: (연결하면 여기 채우기)
};

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

// carrier-sense 로 바이너리 프레임 전송 (조용할 때 + 랜덤 백오프)
void csSendBytes(const uint8_t* buf, int len) {
  for (int t = 0; t < CS_MAX_TRIES; t++) {
    drainBus();
    if (millis() - lastBusRxMs >= BUS_IDLE_MS) {
      // 백오프는 순수 랜덤(ID 편향 X → 공정). de-sync 는 보드별 randomSeed 로 이미 확보.
      unsigned long backoff = 1 + (unsigned long)random(0, 12);
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

void sendFrame(uint8_t type, uint8_t cell, const byte* uid, uint16_t state) {
  uint8_t f[FRAME_LEN];
  f[0] = STX;
  f[1] = type;
  f[2] = SLAVE_ID;
  f[3] = cell;
  if (type == T_HEART) {
    f[4] = (uint8_t)(state & 0xFF);
    f[5] = (uint8_t)((state >> 8) & 0xFF);
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
  const LedMap& m = LED_MAP[busIndex];
  if (m.strip < 0) return;                        // 이 슬롯은 LED 없음 → 무시
  Adafruit_NeoPixel& strip = (m.strip == 0) ? ringLow : ringHigh;
  for (int j = 0; j < LEDS_PER_RING; j++) strip.setPixelColor(m.offset + j, color);
  strip.show();
}

bool sameAddr(const byte a[8], const byte b[8]) {
  for (int i = 0; i < 8; i++) if (a[i] != b[i]) return false;
  return true;
}

void sendHeartbeat() {
  uint16_t state = 0;
  for (int i = 0; i < BUS_COUNT; i++) if (hasLast[i]) state |= (uint16_t)(1u << i);
  sendFrame(T_HEART, 0, nullptr, state);
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
  setRing(i, Adafruit_NeoPixel::Color(255, 147, 41));   // 감지 → 주황 점등
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

  ringLow.begin();
  ringLow.setBrightness(RING_BRIGHTNESS);
  ringLow.clear();
  ringLow.show();

  ringHigh.begin();
  ringHigh.setBrightness(RING_BRIGHTNESS);
  ringHigh.clear();
  ringHigh.show();

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
