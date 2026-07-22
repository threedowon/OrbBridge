/*
 * OrbBridge — Slave ESP32-S3  (폴링 방식)
 *
 * 16× OneWire → DS2431
 * RS485: master 가 P<id> 로 호출할 때만 응답 (충돌 원천 차단)
 *   - 오브 감지 시 즉시 전송하지 않고 큐에 적재
 *   - 자기 차례(P<SLAVE_ID>)에 큐를 비우고 heartbeat(+state) 전송
 *   - OneWire 버스 하나 읽을 때마다 RS485 수신을 확인해 응답 턴어라운드 최소화
 * NeoPixel 링 16개 (각 12 LED) → GPIO 38 체인, DS2431 1개당 링 1개
 *
 * RS485 핀·속도: TX=20, RX=21, 38400 8N1 (모듈 한계로 낮춤)
 */

#include <OneWire.h>
#include <Adafruit_NeoPixel.h>
#include <HardwareSerial.h>

#define TX_PIN       20
#define RX_PIN       21
#define RGB_PIN      48
#define LED_BRIGHTNESS 32   // 255의 1/8
// 저가형 auto-direction 트랜시버가 송신 시작/끝 바이트를 갉아먹어 고속에서 깨진다.
// 폴링이라 충돌은 baud 와 무관하게 0 → 깨끗해질 때까지 내려도 안전(지연만 소폭↑).
// 순서: 38400 → 여전히 깨지면 19200 → 9600. (원래 9600에서 멀쩡히 돌던 시스템)
#define RS485_BAUD   9600
#define SLAVE_ID     6   // 보드마다 고유하게 (1,2,3…). master 의 POLL_IDS 와 일치해야 함
#define HEARTBEAT_MS 1000   // 폴 응답에 heartbeat(+state)를 실어 보내는 최소 주기(ms)

// NeoPixel 링: DS2431 버스 1개당 12 LED 링 1개, GPIO 38 한 데이터선에 체인
#define RING_PIN        38
#define LEDS_PER_RING   12   // 링 1개 = 12 LED = DS2431 1개
#define RING_BRIGHTNESS 32

#define DEBUG_USB    0   // 1: USB Serial Monitor에도 상태 출력

Adafruit_NeoPixel led(1, RGB_PIN, NEO_GRB + NEO_KHZ800);
HardwareSerial RS485(1);

const int BUS_COUNT = 16;
const int busPins[BUS_COUNT] = {
  4, 5, 6, 7,
  15, 16, 17, 18,
  8, 3, 9, 10,
  11, 12, 13, 14
};

OneWire buses[BUS_COUNT] = {
  OneWire(4), OneWire(5), OneWire(6), OneWire(7),
  OneWire(15), OneWire(16), OneWire(17), OneWire(18),
  OneWire(8), OneWire(3), OneWire(9), OneWire(10),
  OneWire(11), OneWire(12), OneWire(13), OneWire(14)
};

// 버스 i → 링 i (LED [i*12 … i*12+11]). 전체 링은 GPIO 38 한 선에 직렬 연결.
Adafruit_NeoPixel ring(BUS_COUNT * LEDS_PER_RING, RING_PIN, NEO_GRB + NEO_KHZ800);

byte lastAddr[BUS_COUNT][8];
bool hasLast[BUS_COUNT] = {false};
unsigned long lastHeartbeatMs = 0;

// ── 이벤트 큐 (감지 → 적재, 폴 응답 시 flush) ────────────
// 오버플로 시 드롭되어도 heartbeat 의 state mask 로 PC 가 자가복구하므로 안전.
struct PendingEvt { uint8_t isAdded; uint8_t bus; byte addr[8]; };
const int MAX_QUEUE = 24;
PendingEvt evtQueue[MAX_QUEUE];
int queueCount = 0;

// ── 온보드 RGB 논블로킹 플래시 (delay 로 poll 반응을 막지 않도록) ──
unsigned long ledOffAt = 0;

// ── RS485 수신 파서 상태 ─────────────────────────────────
String rxLine;
bool rxOverflow = false;

void queueEvent(uint8_t isAdded, int bus, const byte addr[8]) {
  if (queueCount >= MAX_QUEUE) return;   // 오버플로 → PC self-heal 에 위임
  evtQueue[queueCount].isAdded = isAdded;
  evtQueue[queueCount].bus = (uint8_t)bus;
  memcpy(evtQueue[queueCount].addr, addr, 8);
  queueCount++;
}

void flashRed() {
  led.setPixelColor(0, led.Color(255, 0, 0));
  led.show();
  ledOffAt = millis() + 80;   // 논블로킹: loop 에서 시간 지나면 끈다
}

void updateLed() {
  if (ledOffAt != 0 && millis() >= ledOffAt) {
    led.clear();
    led.show();
    ledOffAt = 0;
  }
}

// busIndex 번 링(LED 12개)을 color 로 채운다. color=0 이면 소등.
void setRing(int busIndex, uint32_t color) {
  int base = busIndex * LEDS_PER_RING;
  for (int j = 0; j < LEDS_PER_RING; j++) {
    ring.setPixelColor(base + j, color);
  }
  ring.show();
}

bool sameAddr(const byte a[8], const byte b[8]) {
  for (int i = 0; i < 8; i++) {
    if (a[i] != b[i]) return false;
  }
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

void sendEventLine(uint8_t isAdded, int bus, const byte addr[8]) {
  String msg = "{\"type\":\"";
  msg += (isAdded ? "added" : "removed");
  msg += "\",\"slave\":";
  msg += SLAVE_ID;
  msg += ",\"cell\":";
  msg += bus + 1;
  msg += ",\"id\":\"";
  appendAddr(msg, addr);
  msg += "\"}";
  RS485.println(msg);
}

// heartbeat: "state" = 16칸 점유 비트마스크(bit i = cell i+1). PC 가 이걸로 유실된
// added/removed 를 자가복구(reconcile). 비트마스크는 UID 를 담지 않으므로 PC 는
// "점유 여부"만 신뢰하고, 정체(voice)는 실제 added 이벤트로만 채운다.
void sendHeartbeatLine() {
  uint16_t state = 0;
  for (int i = 0; i < BUS_COUNT; i++) {
    if (hasLast[i]) state |= (uint16_t)(1u << i);
  }
  String msg = "{\"type\":\"heartbeat\",\"slave\":";
  msg += SLAVE_ID;
  msg += ",\"state\":";
  msg += state;
  msg += "}";
  RS485.println(msg);
}

// P<SLAVE_ID> 를 받았을 때: 큐를 비우고 heartbeat(주기적)와 터미네이터를 보낸다.
void respondToPoll() {
  for (int i = 0; i < queueCount; i++) {
    sendEventLine(evtQueue[i].isAdded, evtQueue[i].bus, evtQueue[i].addr);
  }
  queueCount = 0;

  // heartbeat 는 과하지 않게 주기적으로만 (USB 대역·로그 절약)
  if (millis() - lastHeartbeatMs >= HEARTBEAT_MS) {
    sendHeartbeatLine();
    lastHeartbeatMs = millis();
  }

  // 응답 끝 표시(터미네이터). '{' 로 시작하지 않는 줄이라 master 가 PC 로 relay 하지 않고
  // "이 slave 응답 완료 + 생존" 판정에만 쓴다.
  RS485.write((uint8_t)0x04);
  RS485.write((uint8_t)'\n');
  RS485.flush();
}

// OneWire 스캔과 스캔 사이에 자주 호출 → 폴 명령을 즉시 처리(턴어라운드 최소).
// 반이중 에코로 자기 응답이 되돌아와도 'P' 로 시작하지 않으면 무시되므로 안전.
void serviceRS485() {
  while (RS485.available()) {
    char c = (char)RS485.read();
    if (c == '\n') {
      if (!rxOverflow && rxLine.length() >= 2 && rxLine[0] == 'P') {
        if (rxLine.substring(1).toInt() == SLAVE_ID) {
          respondToPoll();
        }
      }
      rxLine = "";
      rxOverflow = false;
      continue;
    }
    if (c == '\r') continue;
    if (rxLine.length() < 12) rxLine += c;   // 폴 명령은 짧다("P16")
    else rxOverflow = true;                  // 에코된 JSON 등 긴 줄 → 이 줄 무시
  }
}

bool readBus(int i, byte addr[8]) {
  buses[i].reset_search();
  if (!buses[i].search(addr)) {
    return false;
  }
  if (OneWire::crc8(addr, 7) != addr[7]) {
#if DEBUG_USB
    Serial.print("CRC error cell ");
    Serial.println(i + 1);
#endif
    return false;
  }
  return true;
}

void onAdded(int i, const byte addr[8]) {
  queueEvent(1, i, addr);
  setRing(i, ring.Color(255, 147, 41));   // 감지 → 따뜻한 노란색 점등(즉시 시각 피드백)
  flashRed();
}

void onRemoved(int i, const byte addr[8]) {
  queueEvent(0, i, addr);
  setRing(i, 0);                           // 링 소등
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
  rxLine.reserve(16);
}

void loop() {
  for (int i = 0; i < BUS_COUNT; i++) {
    scanBus(i);
    serviceRS485();   // 버스 하나 읽을 때마다 폴 확인 → 턴어라운드 ~1ms
    updateLed();
  }
  serviceRS485();
  updateLed();
  delay(1);           // CPU 여유 (poll 은 위 인터리브로 이미 자주 확인됨)
}
