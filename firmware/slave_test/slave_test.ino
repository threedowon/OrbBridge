/*
 * OrbBridge — Slave 단독 점검 펌웨어 (ESP32-S3)
 *
 * master 없이 slave 보드 한 장을 USB 로만 PC 에 연결해 다음을 확인한다.
 *   1. 회로: 16개 OneWire DQ 선이 오브 없을 때 HIGH 로 떠 있는지(풀업·단락 검사),
 *      DIP 스위치 3개가 읽히는지, LED 체인 2개가 각각 켜지는지.
 *   2. DS2431 접점: 셀별 인식·CRC·family code, 접촉 튐(bounce) 횟수,
 *      실제 프로토콜(Read ROM + Read Memory)로 데이터가 오가는지(M 명령).
 *   3. LED: 슬롯별 링 점등(L), 순차 점등(W)으로 LED_MAP 배선이 맞는지,
 *      체인별 점등(A1/A2), slave 와 같은 이펙트(C0~C9).
 *
 * 핀 배치·RS485 프레임은 slave.ino 와 완전히 같다. RS485 로 heartbeat 와
 * added/removed 프레임도 그대로 내보내므로 master 를 붙이면 master 진단
 * 페이지에서도 이 보드가 보인다. 차이는 USB Serial(115200)로 JSON 상태를
 * 계속 내보내고 텍스트 명령을 받는다는 점뿐이다.
 *
 * PC → 보드 (한 줄에 한 명령, '\n' 종료)
 *   ?          hello (펌웨어·ID·핀 배치)
 *   S          status 즉시 출력
 *   C<n>       LED 이펙트 0~9 (slave 와 동일: 0=오브 따라감 1=흰색 2=끄기 3=빨강
 *              4=초록 5=파랑 6=무지개 7=체이스 8=숨쉬기 9=무지개 회전)
 *   L<n>       슬롯 n(1~16) 링만 점등, L0 = 전체 끄기
 *   W          슬롯 1→16 순차 점등(500ms 간격, 다른 명령까지 반복)
 *   A1 / A2    LED 체인 1(GPIO2) / 체인 2(GPIO38) 만 흰색
 *   B<n>       링 밝기 0~255
 *   M<n>       셀 n 의 DS2431 통신 테스트 5회 (M0 = 오브 있는 모든 셀)
 *   R          RS485 로 heartbeat 프레임 1개 전송
 *   Z          카운터 초기화
 *
 * 보드 → PC (JSON 한 줄씩)
 *   {"type":"hello", ...}                      부팅·? 응답
 *   {"type":"status", ...cells:[...]}          1초마다
 *   {"type":"added","cell":n,"gpio":g,"id":..} 오브 인식(디바운스 후)
 *   {"type":"removed","cell":n,"held_ms":..}   오브 제거(REMOVE_DEBOUNCE_MS 뒤)
 *   {"type":"bounce","cell":n,"gap_ms":..}     짧은 접촉 끊김 뒤 복귀
 *   {"type":"memtest","cell":n,"ok":k,"tries":5,"data":"..."}
 *   {"type":"walk","slot":n}                   W 진행 중 현재 슬롯
 *   {"type":"ack","cmd":"..."}                 명령 수신 확인
 *   {"type":"rs485", ...}                      RS485 송수신 알림
 */

#include <OneWire.h>
#include <Adafruit_NeoPixel.h>
#include <HardwareSerial.h>

#define FW_NAME  "slave_test"
#define FW_VER   1

// ── 핀 배치 (slave.ino 와 동일) ─────────────────────────
#define TX_PIN       39
#define RX_PIN       40
#define DE_PIN       42
#define RGB_PIN      48
#define LED_BRIGHTNESS 32
#define RS485_BAUD   115200

#define PIN_DIP_1    41     // +1
#define PIN_DIP_2    47     // +2
#define PIN_DIP_3    21     // +4
uint8_t slaveId = 0;
uint8_t dipBits[3] = {0, 0, 0};

#define HEARTBEAT_MS 2000
#define STATUS_MS    1000
#define REMOVE_DEBOUNCE_MS 300
#define FADE_STEP_MS 30
#define WALK_STEP_MS 500
#define MEMTEST_TRIES 5

#define RING_PIN_LOW    2
#define RING_PIN_HIGH   38
#define LEDS_PER_RING   10
#define LED_COUNT_LOW   80
#define LED_COUNT_HIGH  80
#define RING_BRIGHTNESS 50

#define BUS_IDLE_MS  1
#define CS_MAX_TRIES 20
#define FRAME_REPEAT       3
#define REPEAT_GAP_MIN_MS  5
#define REPEAT_GAP_RAND_MS 10

#define FRAME_LEN 13
#define STX       0x02
#define T_ADDED   1
#define T_REMOVED 2
#define T_HEART   3

#define DS2431_FAMILY 0x2D

Adafruit_NeoPixel led(1, RGB_PIN, NEO_GRB + NEO_KHZ800);
HardwareSerial RS485(1);

const int BUS_COUNT = 16;
const int busPins[BUS_COUNT] = {
  4, 5, 6, 7,
  15, 16, 17, 18,
  8, 1, 9, 10,
  11, 12, 13, 14
};
OneWire buses[BUS_COUNT] = {
  OneWire(4), OneWire(5), OneWire(6), OneWire(7),
  OneWire(15), OneWire(16), OneWire(17), OneWire(18),
  OneWire(8), OneWire(1), OneWire(9), OneWire(10),
  OneWire(11), OneWire(12), OneWire(13), OneWire(14)
};

Adafruit_NeoPixel ringLow (LED_COUNT_LOW,  RING_PIN_LOW,  NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel ringHigh(LED_COUNT_HIGH, RING_PIN_HIGH, NEO_GRB + NEO_KHZ800);

struct LedMap { int8_t strip; int16_t offset; };
const LedMap LED_MAP[BUS_COUNT] = {
  { 0,  0}, { 0, 10}, { 0, 20}, { 0, 30},
  { 0, 40}, { 0, 50}, { 0, 60}, { 0, 70},
  { 1,  0}, { 1, 10}, { 1, 20}, { 1, 30},
  { 1, 40}, { 1, 50}, { 1, 60}, { 1, 70},
};

// ── 셀 상태·통계 ────────────────────────────────────────
struct Cell {
  bool raw;                 // 이번 스캔 raw 판독 (디바운스 전)
  bool has;                 // 디바운스 후 오브 있음
  byte addr[8];
  bool missing;             // 미검출 구간(디바운스 중)
  unsigned long missSinceMs;
  unsigned long sinceMs;    // has 가 true 가 된 시각
  uint8_t level;            // 링 밝기 0~255
  unsigned long lastFadeMs;
  uint32_t flips;           // raw 전환 횟수
  uint32_t bounces;         // 디바운스 안에 복귀한 짧은 끊김 횟수
  uint32_t crcErr;          // ROM CRC 실패
  uint32_t badFamily;       // family code != 0x2D
  uint32_t adds, removes;
  bool stuckLow;            // 오브 없는데 DQ 가 LOW (풀업 없음·단락)
  bool everSeen;            // 이번 세션에 오브가 한 번이라도 인식됨
};
Cell cells[BUS_COUNT];

unsigned long lastHeartbeatMs = 0;
unsigned long lastStatusMs = 0;
unsigned long lastBusRxMs = 0;
unsigned long ledOffAt = 0;
uint32_t rs485RxBytes = 0;
uint32_t rs485TxFrames = 0;

// LED 이펙트: 0~9 는 slave 와 동일, 10=단일 슬롯 11=순차 점등 12=체인1 13=체인2
int ledEffect = 0;
int effectParam = 0;
unsigned long effectStart = 0;
unsigned long lastAnimMs = 0;
int walkSlot = -1;
uint8_t ringBrightness = RING_BRIGHTNESS;
String cmdLine;    // RS485 에서 오는 master 명령
String usbLine;    // USB 에서 오는 PC 명령

void applyEffectOnce();

// ── 유틸 ────────────────────────────────────────────────
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

bool sameAddr(const byte a[8], const byte b[8]) {
  for (int i = 0; i < 8; i++) if (a[i] != b[i]) return false;
  return true;
}

void printHex8(const byte* a) {
  const char* HX = "0123456789ABCDEF";
  for (int i = 0; i < 8; i++) {
    if (i) Serial.print(':');
    Serial.print(HX[(a[i] >> 4) & 0x0F]);
    Serial.print(HX[a[i] & 0x0F]);
  }
}

// ── RS485 (slave.ino 와 동일 동작) ──────────────────────
void drainBus() {
  while (RS485.available()) {
    char c = (char)RS485.read();
    lastBusRxMs = millis();
    rs485RxBytes++;
    if (c == '\n') {
      if (cmdLine.length() >= 2 && cmdLine[0] == 'C') {
        ledEffect = cmdLine.substring(1).toInt();
        effectStart = millis();
        applyEffectOnce();
        Serial.print("{\"type\":\"rs485\",\"rx_cmd\":\"");
        Serial.print(cmdLine);
        Serial.println("\"}");
      }
      cmdLine = "";
    } else if (c != '\r') {
      if (cmdLine.length() < 8) cmdLine += c;
      else cmdLine = "";
    }
  }
}

void csSendBytes(const uint8_t* buf, int len) {
  for (int t = 0; t < CS_MAX_TRIES; t++) {
    drainBus();
    if (millis() - lastBusRxMs >= BUS_IDLE_MS) {
      unsigned long backoff = 1 + (unsigned long)random(0, 12);
      unsigned long t0 = millis();
      while (millis() - t0 < backoff) drainBus();
      if (millis() - lastBusRxMs >= BUS_IDLE_MS) {
        RS485.write(buf, len);
        RS485.flush();
        lastBusRxMs = millis();
        rs485TxFrames++;
        return;
      }
    } else {
      delay(1);
    }
  }
}

void sendFrame(uint8_t type, uint8_t cell, const byte* uid, uint16_t state) {
  uint8_t f[FRAME_LEN];
  f[0] = STX;
  f[1] = type;
  f[2] = slaveId;
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

void pumpFor(unsigned long ms) {
  unsigned long t0 = millis();
  while (millis() - t0 < ms) drainBus();
}

void sendFrameRepeated(uint8_t type, uint8_t cell, const byte* uid) {
  for (int r = 0; r < FRAME_REPEAT; r++) {
    sendFrame(type, cell, uid, 0);
    if (r + 1 < FRAME_REPEAT) {
      pumpFor(REPEAT_GAP_MIN_MS + (unsigned long)random(0, REPEAT_GAP_RAND_MS));
    }
  }
}

uint16_t stateMask() {
  uint16_t state = 0;
  for (int i = 0; i < BUS_COUNT; i++) if (cells[i].has) state |= (uint16_t)(1u << i);
  return state;
}

void sendHeartbeat() { sendFrame(T_HEART, 0, nullptr, stateMask()); }

// ── LED ─────────────────────────────────────────────────
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

void setRingNoShow(int busIndex, uint32_t color) {
  const LedMap& m = LED_MAP[busIndex];
  if (m.strip < 0) return;
  Adafruit_NeoPixel& strip = (m.strip == 0) ? ringLow : ringHigh;
  for (int j = 0; j < LEDS_PER_RING; j++) strip.setPixelColor(m.offset + j, color);
}

void showBoth() { ringLow.show(); ringHigh.show(); }

void setRing(int busIndex, uint32_t color) {
  setRingNoShow(busIndex, color);
  const LedMap& m = LED_MAP[busIndex];
  if (m.strip == 0) ringLow.show(); else if (m.strip == 1) ringHigh.show();
}

void setSlotLevel(int i, uint8_t level) {
  cells[i].level = level;
  if (ledEffect != 0) return;
  setRing(i, Adafruit_NeoPixel::Color(
    (uint8_t)((uint16_t)255 * level / 255),
    (uint8_t)((uint16_t)147 * level / 255),
    (uint8_t)((uint16_t)41  * level / 255)));
}

// 이펙트 0(오브 따라감)으로 돌아올 때 현재 배치대로 링을 다시 그린다.
void redrawFollow() {
  for (int i = 0; i < BUS_COUNT; i++) {
    uint8_t level = cells[i].level;
    setRingNoShow(i, Adafruit_NeoPixel::Color(
      (uint8_t)((uint16_t)255 * level / 255),
      (uint8_t)((uint16_t)147 * level / 255),
      (uint8_t)((uint16_t)41  * level / 255)));
  }
  showBoth();
}

void fillAll(uint32_t color) {
  for (uint16_t i = 0; i < LED_COUNT_LOW; i++)  ringLow.setPixelColor(i, color);
  for (uint16_t i = 0; i < LED_COUNT_HIGH; i++) ringHigh.setPixelColor(i, color);
  showBoth();
}

void fillChain(int chain, uint32_t color) {
  for (uint16_t i = 0; i < LED_COUNT_LOW; i++)  ringLow.setPixelColor(i, chain == 1 ? color : 0);
  for (uint16_t i = 0; i < LED_COUNT_HIGH; i++) ringHigh.setPixelColor(i, chain == 2 ? color : 0);
  showBoth();
}

void rainbowStatic() {
  for (uint16_t i = 0; i < LED_COUNT_LOW; i++)
    ringLow.setPixelColor(i, Adafruit_NeoPixel::ColorHSV((uint16_t)(i * 65536L / LED_COUNT_LOW)));
  for (uint16_t i = 0; i < LED_COUNT_HIGH; i++)
    ringHigh.setPixelColor(i, Adafruit_NeoPixel::ColorHSV((uint16_t)(i * 65536L / LED_COUNT_HIGH)));
  showBoth();
}

void applyEffectOnce() {
  switch (ledEffect) {
    case 0: redrawFollow(); break;
    case 1: fillAll(Adafruit_NeoPixel::Color(255, 255, 255)); break;
    case 2: fillAll(0); break;
    case 3: fillAll(Adafruit_NeoPixel::Color(255, 0, 0)); break;
    case 4: fillAll(Adafruit_NeoPixel::Color(0, 255, 0)); break;
    case 5: fillAll(Adafruit_NeoPixel::Color(0, 0, 255)); break;
    case 6: rainbowStatic(); break;
    case 10:   // 단일 슬롯
      fillAll(0);
      if (effectParam >= 1 && effectParam <= BUS_COUNT)
        setRing(effectParam - 1, Adafruit_NeoPixel::Color(255, 255, 255));
      break;
    case 11:   // 순차 점등 — runEffectAnim 에서 진행
      walkSlot = -1;
      lastAnimMs = 0;
      fillAll(0);
      break;
    case 12: fillChain(1, Adafruit_NeoPixel::Color(255, 255, 255)); break;
    case 13: fillChain(2, Adafruit_NeoPixel::Color(255, 255, 255)); break;
    default: break;   // 7~9 애니메이션
  }
}

void runEffectAnim() {
  if (ledEffect == 11) {
    if (millis() - lastAnimMs < WALK_STEP_MS) return;
    lastAnimMs = millis();
    walkSlot = (walkSlot + 1) % BUS_COUNT;
    for (int i = 0; i < BUS_COUNT; i++)
      setRingNoShow(i, i == walkSlot ? Adafruit_NeoPixel::Color(0, 200, 255) : 0);
    showBoth();
    Serial.print("{\"type\":\"walk\",\"slot\":");
    Serial.print(walkSlot + 1);
    Serial.print(",\"gpio\":");
    Serial.print(busPins[walkSlot]);
    Serial.println("}");
    return;
  }
  if (ledEffect < 7 || ledEffect > 9) return;
  if (millis() - lastAnimMs < 30) return;
  lastAnimMs = millis();
  unsigned long t = millis() - effectStart;

  if (ledEffect == 7) {
    int total = LED_COUNT_LOW;
    int pos = (t / 60) % total;
    uint32_t on = Adafruit_NeoPixel::Color(0, 180, 255);
    for (uint16_t i = 0; i < LED_COUNT_LOW; i++)
      ringLow.setPixelColor(i, ((int)i == pos || (int)i == (pos + 1) % total) ? on : 0);
    for (uint16_t i = 0; i < LED_COUNT_HIGH; i++)
      ringHigh.setPixelColor(i, ((int)i == pos || (int)i == (pos + 1) % total) ? on : 0);
    showBoth();
  } else if (ledEffect == 8) {
    float ph = (t % 2000) / 2000.0f;
    int b = (int)((0.5f - 0.5f * cos(ph * 2 * PI)) * 255);
    fillAll(Adafruit_NeoPixel::Color((b * 60) / 255, (b * 120) / 255, (uint8_t)b));
  } else if (ledEffect == 9) {
    uint16_t base = (uint16_t)(t * 8);
    for (uint16_t i = 0; i < LED_COUNT_LOW; i++)
      ringLow.setPixelColor(i, Adafruit_NeoPixel::ColorHSV(base + (uint16_t)(i * 65536L / LED_COUNT_LOW)));
    for (uint16_t i = 0; i < LED_COUNT_HIGH; i++)
      ringHigh.setPixelColor(i, Adafruit_NeoPixel::ColorHSV(base + (uint16_t)(i * 65536L / LED_COUNT_HIGH)));
    showBoth();
  }
}

void setEffect(int eff, int param) {
  ledEffect = eff;
  effectParam = param;
  effectStart = millis();
  applyEffectOnce();
}

// ── DS2431 ──────────────────────────────────────────────
bool readBus(int i, byte addr[8]) {
  Cell& c = cells[i];
  buses[i].reset_search();
  if (!buses[i].search(addr)) return false;
  if (OneWire::crc8(addr, 7) != addr[7]) { c.crcErr++; return false; }
  if (addr[0] != DS2431_FAMILY) { c.badFamily++; }   // 다른 1-Wire 칩 — 인식은 하되 표시
  return true;
}

// 오브가 없을 때 DQ 가 LOW 면 풀업이 없거나 GND 로 단락된 것이다.
// OneWire 는 동작 뒤 핀을 입력으로 풀어두므로 그대로 읽으면 된다.
bool lineIdleLow(int i) {
  pinMode(busPins[i], INPUT);
  delayMicroseconds(5);
  return digitalRead(busPins[i]) == LOW;
}

// 실제 프로토콜로 통신이 되는지 검사한다: Read ROM(0x33) 으로 받은 8B 가
// CRC 를 통과하고 search 결과와 같으면 성공. 이어서 Read Memory(0xF0) 로
// 주소 0 부터 8B 를 읽어 data 로 보여준다(CRC 없음, 참고용).
int memTest(int i, byte data[8]) {
  Cell& c = cells[i];
  OneWire& ow = buses[i];
  int ok = 0;
  for (int t = 0; t < MEMTEST_TRIES; t++) {
    byte rom[8];
    if (!ow.reset()) continue;
    ow.write(0x33);
    for (int k = 0; k < 8; k++) rom[k] = ow.read();
    if (OneWire::crc8(rom, 7) != rom[7]) continue;
    if (!sameAddr(rom, c.addr)) continue;
    if (!ow.reset()) continue;
    ow.select(c.addr);
    ow.write(0xF0);
    ow.write(0x00);
    ow.write(0x00);
    for (int k = 0; k < 8; k++) data[k] = ow.read();
    ow.reset();
    ok++;
  }
  return ok;
}

void emitMemTest(int i) {
  Cell& c = cells[i];
  byte data[8] = {0};
  unsigned long t0 = millis();
  int ok = c.has ? memTest(i, data) : 0;
  Serial.print("{\"type\":\"memtest\",\"cell\":");
  Serial.print(i + 1);
  Serial.print(",\"gpio\":");
  Serial.print(busPins[i]);
  Serial.print(",\"present\":");
  Serial.print(c.has ? 1 : 0);
  Serial.print(",\"ok\":");
  Serial.print(ok);
  Serial.print(",\"tries\":");
  Serial.print(MEMTEST_TRIES);
  Serial.print(",\"ms\":");
  Serial.print(millis() - t0);
  Serial.print(",\"id\":\"");
  if (c.has) printHex8(c.addr);
  Serial.print("\",\"data\":\"");
  if (ok) printHex8(data);
  Serial.println("\"}");
}

// ── 이벤트 출력 ─────────────────────────────────────────
void emitAdded(int i) {
  Cell& c = cells[i];
  Serial.print("{\"type\":\"added\",\"cell\":");
  Serial.print(i + 1);
  Serial.print(",\"gpio\":");
  Serial.print(busPins[i]);
  Serial.print(",\"id\":\"");
  printHex8(c.addr);
  Serial.print("\",\"family\":\"");
  Serial.print(c.addr[0], HEX);
  Serial.println("\"}");
}

void emitRemoved(int i, unsigned long heldMs) {
  Cell& c = cells[i];
  Serial.print("{\"type\":\"removed\",\"cell\":");
  Serial.print(i + 1);
  Serial.print(",\"gpio\":");
  Serial.print(busPins[i]);
  Serial.print(",\"id\":\"");
  printHex8(c.addr);
  Serial.print("\",\"held_ms\":");
  Serial.print(heldMs);
  Serial.println("}");
}

void emitBounce(int i, unsigned long gapMs) {
  Serial.print("{\"type\":\"bounce\",\"cell\":");
  Serial.print(i + 1);
  Serial.print(",\"gpio\":");
  Serial.print(busPins[i]);
  Serial.print(",\"gap_ms\":");
  Serial.print(gapMs);
  Serial.println("}");
}

void emitHello() {
  Serial.print("{\"type\":\"hello\",\"fw\":\"" FW_NAME "\",\"ver\":");
  Serial.print(FW_VER);
  Serial.print(",\"id\":");
  Serial.print(slaveId);
  Serial.print(",\"dip\":[");
  Serial.print(dipBits[0]); Serial.print(','); Serial.print(dipBits[1]); Serial.print(','); Serial.print(dipBits[2]);
  Serial.print("],\"buses\":[");
  for (int i = 0; i < BUS_COUNT; i++) { if (i) Serial.print(','); Serial.print(busPins[i]); }
  Serial.print("],\"leds\":{\"chain1_pin\":");
  Serial.print(RING_PIN_LOW);
  Serial.print(",\"chain2_pin\":");
  Serial.print(RING_PIN_HIGH);
  Serial.print(",\"per_ring\":");
  Serial.print(LEDS_PER_RING);
  Serial.print(",\"count\":[");
  Serial.print(LED_COUNT_LOW); Serial.print(','); Serial.print(LED_COUNT_HIGH);
  Serial.print("],\"map\":[");
  for (int i = 0; i < BUS_COUNT; i++) {
    if (i) Serial.print(',');
    Serial.print('['); Serial.print(LED_MAP[i].strip + 1); Serial.print(','); Serial.print(LED_MAP[i].offset); Serial.print(']');
  }
  Serial.print("]},\"rs485\":{\"tx\":");
  Serial.print(TX_PIN);
  Serial.print(",\"rx\":");
  Serial.print(RX_PIN);
  Serial.print(",\"de\":");
  Serial.print(DE_PIN);
  Serial.print(",\"baud\":");
  Serial.print(RS485_BAUD);
  Serial.print("},\"dip_pins\":[");
  Serial.print(PIN_DIP_1); Serial.print(','); Serial.print(PIN_DIP_2); Serial.print(','); Serial.print(PIN_DIP_3);
  Serial.println("]}");
}

void emitStatus() {
  Serial.print("{\"type\":\"status\",\"fw\":\"" FW_NAME "\",\"id\":");
  Serial.print(slaveId);
  Serial.print(",\"dip\":[");
  Serial.print(dipBits[0]); Serial.print(','); Serial.print(dipBits[1]); Serial.print(','); Serial.print(dipBits[2]);
  Serial.print("],\"up\":");
  Serial.print(millis());
  Serial.print(",\"state\":");
  Serial.print(stateMask());
  Serial.print(",\"effect\":");
  Serial.print(ledEffect);
  Serial.print(",\"bright\":");
  Serial.print(ringBrightness);
  Serial.print(",\"rs485_rx\":");
  Serial.print(rs485RxBytes);
  Serial.print(",\"rs485_tx\":");
  Serial.print(rs485TxFrames);
  Serial.print(",\"cells\":[");
  for (int i = 0; i < BUS_COUNT; i++) {
    Cell& c = cells[i];
    if (i) Serial.print(',');
    Serial.print("{\"c\":");
    Serial.print(i + 1);
    Serial.print(",\"g\":");
    Serial.print(busPins[i]);
    Serial.print(",\"p\":");
    Serial.print(c.has ? 1 : 0);
    Serial.print(",\"r\":");
    Serial.print(c.raw ? 1 : 0);
    Serial.print(",\"id\":\"");
    if (c.has) printHex8(c.addr);
    Serial.print("\",\"held\":");
    Serial.print(c.has ? (millis() - c.sinceMs) : 0);
    Serial.print(",\"crc\":");
    Serial.print(c.crcErr);
    Serial.print(",\"fam\":");
    Serial.print(c.badFamily);
    Serial.print(",\"flip\":");
    Serial.print(c.flips);
    Serial.print(",\"bnc\":");
    Serial.print(c.bounces);
    Serial.print(",\"add\":");
    Serial.print(c.adds);
    Serial.print(",\"rm\":");
    Serial.print(c.removes);
    Serial.print(",\"low\":");
    Serial.print(c.stuckLow ? 1 : 0);
    Serial.print(",\"seen\":");
    Serial.print(c.everSeen ? 1 : 0);
    Serial.print('}');
  }
  Serial.println("]}");
}

void emitAck(const String& cmd, bool ok) {
  Serial.print("{\"type\":\"ack\",\"cmd\":\"");
  Serial.print(cmd);
  Serial.print("\",\"ok\":");
  Serial.print(ok ? 1 : 0);
  Serial.println("}");
}

// ── 스캔 ────────────────────────────────────────────────
void onAdded(int i) {
  Cell& c = cells[i];
  c.adds++;
  c.everSeen = true;
  c.sinceMs = millis();
  setSlotLevel(i, 255);
  flashRed();
  emitAdded(i);
  sendFrameRepeated(T_ADDED, i + 1, c.addr);
}

void onRemoved(int i) {
  Cell& c = cells[i];
  c.removes++;
  setSlotLevel(i, 0);
  emitRemoved(i, millis() - c.sinceMs);
  sendFrameRepeated(T_REMOVED, i + 1, c.addr);
}

void scanBus(int i) {
  Cell& c = cells[i];
  byte addr[8];
  bool present = readBus(i, addr);
  if (present != c.raw) { c.flips++; c.raw = present; }

  if (present) {
    c.stuckLow = false;
    if (c.missing) {                 // 디바운스 안에 돌아옴 = 접촉 튐
      c.bounces++;
      emitBounce(i, millis() - c.missSinceMs);
      c.missing = false;
    }
    if (!c.has) {
      memcpy(c.addr, addr, 8);
      c.has = true;
      onAdded(i);
    } else if (!sameAddr(c.addr, addr)) {
      onRemoved(i);
      memcpy(c.addr, addr, 8);
      c.sinceMs = millis();
      onAdded(i);
    } else if (c.level != 255) {
      setSlotLevel(i, 255);
    }
    return;
  }

  c.stuckLow = lineIdleLow(i);
  if (c.has) {
    if (!c.missing) {
      c.missing = true;
      c.missSinceMs = millis();
      c.lastFadeMs = 0;
    }
    unsigned long gone = millis() - c.missSinceMs;
    if (gone >= REMOVE_DEBOUNCE_MS) {
      onRemoved(i);
      c.has = false;
      c.missing = false;
      memset(c.addr, 0, 8);
    } else if (millis() - c.lastFadeMs >= FADE_STEP_MS) {
      setSlotLevel(i, (uint8_t)(255 - (255UL * gone / REMOVE_DEBOUNCE_MS)));
      c.lastFadeMs = millis();
    }
  } else {
    c.missing = false;
  }
}

// ── USB 명령 ────────────────────────────────────────────
void resetCounters() {
  for (int i = 0; i < BUS_COUNT; i++) {
    Cell& c = cells[i];
    c.flips = c.bounces = c.crcErr = c.badFamily = c.adds = c.removes = 0;
    c.everSeen = c.has;
  }
  rs485RxBytes = 0;
  rs485TxFrames = 0;
}

void handleUsbCommand(const String& line) {
  if (line.length() == 0) return;
  char op = line[0];
  int n = line.length() > 1 ? line.substring(1).toInt() : 0;
  bool ok = true;

  switch (op) {
    case '?': emitHello(); break;
    case 'S': emitStatus(); break;
    case 'C':
      if (n < 0 || n > 9) { ok = false; break; }
      setEffect(n, 0);
      break;
    case 'L':
      if (n < 0 || n > BUS_COUNT) { ok = false; break; }
      if (n == 0) setEffect(2, 0); else setEffect(10, n);
      break;
    case 'W': setEffect(11, 0); break;
    case 'A':
      if (n == 1) setEffect(12, 0);
      else if (n == 2) setEffect(13, 0);
      else ok = false;
      break;
    case 'B':
      if (n < 0 || n > 255) { ok = false; break; }
      ringBrightness = (uint8_t)n;
      ringLow.setBrightness(ringBrightness);
      ringHigh.setBrightness(ringBrightness);
      applyEffectOnce();
      break;
    case 'M':
      if (n < 0 || n > BUS_COUNT) { ok = false; break; }
      if (n == 0) { for (int i = 0; i < BUS_COUNT; i++) if (cells[i].has) emitMemTest(i); }
      else emitMemTest(n - 1);
      break;
    case 'R':
      sendHeartbeat();
      Serial.print("{\"type\":\"rs485\",\"sent\":\"heartbeat\",\"state\":");
      Serial.print(stateMask());
      Serial.println("}");
      break;
    case 'Z': resetCounters(); break;
    default: ok = false; break;
  }
  emitAck(line, ok);
  if (op == 'Z' || op == 'B' || op == 'C' || op == 'L' || op == 'W' || op == 'A') emitStatus();
}

void pollUsb() {
  while (Serial.available()) {
    char ch = (char)Serial.read();
    if (ch == '\n') {
      usbLine.trim();
      handleUsbCommand(usbLine);
      usbLine = "";
    } else if (ch != '\r') {
      if (usbLine.length() < 16) usbLine += ch;
      else usbLine = "";
    }
  }
}

// ── setup / loop ────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(1500);   // USB CDC 가 붙을 시간을 준다 — 부팅 hello 를 놓치지 않도록

  pinMode(PIN_DIP_1, INPUT_PULLUP);
  pinMode(PIN_DIP_2, INPUT_PULLUP);
  pinMode(PIN_DIP_3, INPUT_PULLUP);
  delay(10);
  dipBits[0] = !digitalRead(PIN_DIP_1);
  dipBits[1] = !digitalRead(PIN_DIP_2);
  dipBits[2] = !digitalRead(PIN_DIP_3);
  slaveId = (uint8_t)(dipBits[0] | (dipBits[1] << 1) | (dipBits[2] << 2));

  memset(cells, 0, sizeof(cells));

  led.begin();
  led.setBrightness(LED_BRIGHTNESS);
  led.clear();
  led.show();

  ringLow.begin();
  ringLow.setBrightness(ringBrightness);
  ringLow.clear();
  ringLow.show();

  ringHigh.begin();
  ringHigh.setBrightness(ringBrightness);
  ringHigh.clear();
  ringHigh.show();

  RS485.begin(RS485_BAUD, SERIAL_8N1, RX_PIN, TX_PIN);
  RS485.setPins(RX_PIN, TX_PIN, -1, DE_PIN);
  RS485.setMode(UART_MODE_RS485_HALF_DUPLEX);

  randomSeed(slaveId * 7919 + 13);
  lastHeartbeatMs = millis();

  emitHello();
  emitStatus();
}

void loop() {
  drainBus();
  pollUsb();

  for (int i = 0; i < BUS_COUNT; i++) {
    scanBus(i);
    drainBus();
    updateLed();
  }

  if (millis() - lastHeartbeatMs >= HEARTBEAT_MS) {
    sendHeartbeat();
    lastHeartbeatMs = millis();
  }
  if (millis() - lastStatusMs >= STATUS_MS) {
    emitStatus();
    lastStatusMs = millis();
  }

  runEffectAnim();
  updateLed();
  delay(1);
}
