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

// 접촉 불량으로 OneWire 읽기가 잠깐 실패해도 제거로 보지 않는다.
// 이 시간 동안 계속 못 읽어야 실제 제거로 인정한다. loop 주기가 LED
// 이펙트·RS485 트래픽에 따라 흔들리므로 횟수가 아니라 시간으로 센다.
// 접촉이 계속 튀면 이 값을 키운다(게임 판정이 그만큼 늦어진다).
#define REMOVE_DEBOUNCE_MS 300

// 링 LED 는 손을 뗀 즉시 반응해야 하므로 훨씬 짧은 창을 쓴다. 튐으로 잠깐
// 꺼졌다가 접촉이 돌아오면 다시 켜지고, removed 프레임은 나가지 않는다.
#define LED_OFF_DEBOUNCE_MS 60

// LED 데이터선 2개. 슬롯별 LED 위치는 아래 LED_MAP 으로 배선에 맞게 직접 지정한다.
#define RING_PIN_LOW    38     // LED 스트립 A = 슬롯 1~8  (GPIO 38)
#define RING_PIN_HIGH   39     // LED 스트립 B = 슬롯 9~16 (GPIO 39)
#define LEDS_PER_RING   10     // 슬롯당 LED 수
#define LED_COUNT_LOW   80     // 스트립 A 총 LED 수 (슬롯 1~8 = 8×10)
#define LED_COUNT_HIGH  80     // 스트립 B 총 LED 수 (슬롯 9~16 = 8×10)
// 지금 40 LED(슬롯5~8)만 연결이라 50 으로 올림(~0.4A). LED 많이 늘리면 외부 5V + 밝기 조정.
#define RING_BRIGHTNESS 50

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
  { 0,  0}, { 0, 10}, { 0, 20}, { 0, 30},   // 슬롯 1~4  : 스트립A(GPIO38) 0/10/20/30
  { 0, 40}, { 0, 50}, { 0, 60}, { 0, 70},   // 슬롯 5~8  : 스트립A(GPIO38) 40/50/60/70
  { 1,  0}, { 1, 10}, { 1, 20}, { 1, 30},   // 슬롯 9~12 : 스트립B(GPIO39) 0/10/20/30
  { 1, 40}, { 1, 50}, { 1, 60}, { 1, 70},   // 슬롯 13~16: 스트립B(GPIO39) 40/50/60/70
};

byte lastAddr[BUS_COUNT][8];
bool hasLast[BUS_COUNT] = {false};
bool ringOn[BUS_COUNT] = {false};         // 슬롯 링이 켜져 있는지
bool missing[BUS_COUNT] = {false};        // 미검출 구간에 들어갔는지
unsigned long missSinceMs[BUS_COUNT] = {0};   // 그 구간이 시작된 시각
unsigned long lastHeartbeatMs = 0;
unsigned long lastBusRxMs = 0;
unsigned long ledOffAt = 0;

// LED 테스트 이펙트 (master 가 debug 도구에서 "C<n>" 명령을 RS485 로 브로드캐스트).
//   0=일반(오브 감지)  1=흰색전체  2=끄기  3=빨강 4=초록 5=파랑
//   6=무지개(정지)  7=체이스(움직임)  8=숨쉬기  9=무지개(회전)
int ledEffect = 0;
unsigned long effectStart = 0;
unsigned long lastAnimMs = 0;
String cmdLine;
void applyEffectOnce();   // 전방 선언

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

// RS485 수신 처리: carrier-sense 활동 감지 + master LED 명령("C<n>\n") 파싱.
// slave→master 바이너리 프레임(STX=0x02)은 'C' 로 시작 안 하므로 명령으로 오검출 안 됨.
void drainBus() {
  while (RS485.available()) {
    char c = (char)RS485.read();
    lastBusRxMs = millis();
    if (c == '\n') {
      if (cmdLine.length() >= 2 && cmdLine[0] == 'C') {
        ledEffect = cmdLine.substring(1).toInt();
        effectStart = millis();
        applyEffectOnce();
      }
      cmdLine = "";
    } else if (c != '\r') {
      if (cmdLine.length() < 8) cmdLine += c;
      else cmdLine = "";     // 너무 긴 줄(바이너리 프레임 등) → 명령 아님
    }
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

// 슬롯 링 점등/소등을 한 곳에서 처리한다. 이펙트 모드에서는 링을 건드리지
// 않지만 상태는 계속 추적해, 이펙트를 끄면 실제 배치와 다시 맞는다.
void setSlotLed(int i, bool on) {
  ringOn[i] = on;
  if (ledEffect == 0) {
    setRing(i, on ? Adafruit_NeoPixel::Color(255, 147, 41) : 0);
  }
}

// ── LED 테스트 이펙트 ───────────────────────────────────
void fillAll(uint32_t color) {
  for (uint16_t i = 0; i < LED_COUNT_LOW; i++)  ringLow.setPixelColor(i, color);
  for (uint16_t i = 0; i < LED_COUNT_HIGH; i++) ringHigh.setPixelColor(i, color);
  ringLow.show();
  ringHigh.show();
}

void rainbowStatic() {
  for (uint16_t i = 0; i < LED_COUNT_LOW; i++)
    ringLow.setPixelColor(i, Adafruit_NeoPixel::ColorHSV((uint16_t)(i * 65536L / LED_COUNT_LOW)));
  for (uint16_t i = 0; i < LED_COUNT_HIGH; i++)
    ringHigh.setPixelColor(i, Adafruit_NeoPixel::ColorHSV((uint16_t)(i * 65536L / LED_COUNT_HIGH)));
  ringLow.show();
  ringHigh.show();
}

// 명령 받은 즉시 적용되는 정적 이펙트. 애니메이션(7~9)은 loop 의 runEffectAnim 에서 갱신.
void applyEffectOnce() {
  switch (ledEffect) {
    case 0: fillAll(0); break;                                        // 일반 복귀(끔)
    case 1: fillAll(Adafruit_NeoPixel::Color(255, 255, 255)); break;  // 전체 흰색
    case 2: fillAll(0); break;                                        // 전체 끄기
    case 3: fillAll(Adafruit_NeoPixel::Color(255, 0, 0)); break;      // 빨강
    case 4: fillAll(Adafruit_NeoPixel::Color(0, 255, 0)); break;      // 초록
    case 5: fillAll(Adafruit_NeoPixel::Color(0, 0, 255)); break;      // 파랑
    case 6: rainbowStatic(); break;                                   // 무지개(정지)
    default: break;                                                   // 7~9: 애니메이션
  }
}

// 애니메이션 이펙트 (loop 에서 ~30ms 마다 호출)
void runEffectAnim() {
  if (ledEffect < 7) return;
  if (millis() - lastAnimMs < 30) return;
  lastAnimMs = millis();
  unsigned long t = millis() - effectStart;

  if (ledEffect == 7) {                 // 체이스 (흐르는 빛)
    int total = LED_COUNT_LOW;
    int pos = (t / 60) % total;
    uint32_t on = Adafruit_NeoPixel::Color(0, 180, 255);
    for (uint16_t i = 0; i < LED_COUNT_LOW; i++)
      ringLow.setPixelColor(i, ((int)i == pos || (int)i == (pos + 1) % total) ? on : 0);
    for (uint16_t i = 0; i < LED_COUNT_HIGH; i++)
      ringHigh.setPixelColor(i, ((int)i == pos || (int)i == (pos + 1) % total) ? on : 0);
    ringLow.show(); ringHigh.show();
  } else if (ledEffect == 8) {          // 숨쉬기 (밝기 fade)
    float ph = (t % 2000) / 2000.0f;
    int b = (int)((0.5f - 0.5f * cos(ph * 2 * PI)) * 255);
    fillAll(Adafruit_NeoPixel::Color((b * 60) / 255, (b * 120) / 255, (uint8_t)b));
  } else if (ledEffect == 9) {          // 무지개 회전
    uint16_t base = (uint16_t)(t * 8);
    for (uint16_t i = 0; i < LED_COUNT_LOW; i++)
      ringLow.setPixelColor(i, Adafruit_NeoPixel::ColorHSV(base + (uint16_t)(i * 65536L / LED_COUNT_LOW)));
    for (uint16_t i = 0; i < LED_COUNT_HIGH; i++)
      ringHigh.setPixelColor(i, Adafruit_NeoPixel::ColorHSV(base + (uint16_t)(i * 65536L / LED_COUNT_HIGH)));
    ringLow.show(); ringHigh.show();
  }
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
  setSlotLed(i, true);
  flashRed();
  sendFrame(T_ADDED, i + 1, addr, 0);
}

void onRemoved(int i, const byte addr[8]) {
  setSlotLed(i, false);
  sendFrame(T_REMOVED, i + 1, addr, 0);
}

void scanBus(int i) {
  byte addr[8];
  bool present = readBus(i, addr);
  if (present) {
    missing[i] = false;
    if (!hasLast[i]) {
      memcpy(lastAddr[i], addr, 8);
      hasLast[i] = true;
      onAdded(i, addr);
    } else if (!sameAddr(lastAddr[i], addr)) {
      onRemoved(i, lastAddr[i]);
      memcpy(lastAddr[i], addr, 8);
      onAdded(i, addr);
    } else if (!ringOn[i]) {
      setSlotLed(i, true);   // 튐으로 껐던 링을 접촉 복구와 함께 되살린다
    }
  } else if (hasLast[i]) {
    // 짧은 읽기 실패로 removed 를 보내면 add/remove 가 반복해서 나간다.
    // 링 LED 는 즉시 반응해야 하므로 짧은 창을, removed 프레임은 접촉 튐을
    // 걸러야 하므로 긴 창을 쓴다.
    if (!missing[i]) {
      missing[i] = true;
      missSinceMs[i] = millis();
    }
    unsigned long gone = millis() - missSinceMs[i];
    if (ringOn[i] && gone >= LED_OFF_DEBOUNCE_MS) {
      setSlotLed(i, false);
    }
    if (gone >= REMOVE_DEBOUNCE_MS) {
      onRemoved(i, lastAddr[i]);
      hasLast[i] = false;
      missing[i] = false;
      memset(lastAddr[i], 0, 8);
    }
  } else {
    missing[i] = false;
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

  runEffectAnim();   // 애니메이션 이펙트(7~9) 갱신
  updateLed();
  delay(1);
}
