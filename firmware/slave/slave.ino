/*
 * OrbBridge — Slave ESP32-S3  (폴링 방식)
 *
 * 16× OneWire → DS2431
 * RS485 ↔ Master : master 의 폴 요청("P<id>\n")을 받으면, 그동안 큐에 쌓아둔
 *                  added / removed 이벤트를 몰아서 보내고 heartbeat 로 마무리한다.
 *                  → 자기 차례에만 말하므로 버스 충돌이 없다.
 * NeoPixel 링 16개 (각 12 LED) → GPIO 38 체인, DS2431 1개당 링 1개
 *
 * 감지 즉시 하는 일: 링 LED 갱신(즉각적) + 이벤트를 큐에 적재.
 *   실제 RS485 전송은 master 가 이 slave 를 폴링할 때만 한다.
 *
 * RS485 핀·속도: TX=20, RX=21, 9600. auto-direction 트랜시버 가정(DE/RE 토글 없음).
 */

#include <OneWire.h>
#include <Adafruit_NeoPixel.h>
#include <HardwareSerial.h>

#define TX_PIN       20
#define RX_PIN       21
#define RGB_PIN      48
#define LED_BRIGHTNESS 32   // 255의 1/8
#define RS485_BAUD   9600   // master 와 반드시 동일하게
#define SLAVE_ID     6      // 보드마다 1, 2, 3 … (master 의 POLL_ID 범위 안이어야 함)

// NeoPixel 링: DS2431 버스 1개당 12 LED 링 1개, GPIO 38 한 데이터선에 체인
#define RING_PIN        38
#define LEDS_PER_RING   12   // 링 1개 = 12 LED = DS2431 1개
#define RING_BRIGHTNESS 32

#define DEBUG_USB    0   // 1: USB Serial Monitor에도 JSON 출력

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

// ── 이벤트 큐 (폴링될 때까지 대기) ───────────────────────
struct OrbEvent {
  bool added;      // true=added, false=removed
  int  cell;       // 1..16
  byte addr[8];
};
const int QUEUE_MAX = 32;
OrbEvent queue[QUEUE_MAX];
int qHead = 0, qTail = 0, qCount = 0;

void enqueueEvent(bool added, int cell, const byte addr[8]) {
  if (qCount >= QUEUE_MAX) {
    // 오버플로: 가장 오래된 것 버림 (거의 발생하지 않음)
    qHead = (qHead + 1) % QUEUE_MAX;
    qCount--;
  }
  queue[qTail].added = added;
  queue[qTail].cell  = cell;
  memcpy(queue[qTail].addr, addr, 8);
  qTail = (qTail + 1) % QUEUE_MAX;
  qCount++;
}

String rs485In;   // master 폴 요청 수신 버퍼

void flashRed() {
  led.setPixelColor(0, led.Color(255, 0, 0));
  led.show();
  delay(80);
  led.clear();
  led.show();
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

// 감지 즉시: 링 갱신(즉각) + 이벤트 큐 적재(전송은 폴링 시)
void onAdded(int busIndex, const byte addr[8]) {
  enqueueEvent(true, busIndex + 1, addr);
  setRing(busIndex, ring.Color(255, 147, 41));   // 감지 → 따뜻한 노란색 점등
  flashRed();
}

void onRemoved(int busIndex, const byte addr[8]) {
  enqueueEvent(false, busIndex + 1, addr);
  setRing(busIndex, 0);   // 링 소등
}

void buildEventJson(String& msg, const OrbEvent& e) {
  msg = "{\"type\":\"";
  msg += e.added ? "added" : "removed";
  msg += "\",\"slave\":";
  msg += SLAVE_ID;
  msg += ",\"cell\":";
  msg += e.cell;
  msg += ",\"id\":\"";
  appendAddr(msg, e.addr);
  msg += "\"}";
}

void rs485SendLine(const String& line) {
  RS485.println(line);
  RS485.flush();
#if DEBUG_USB
  Serial.println(line);
#endif
}

// master 가 이 slave 를 호출했을 때: 큐를 비우고 heartbeat 로 마무리
void respondToPoll() {
  delay(2);  // master 가 송신 종료 후 RX 로 전환할 시간을 약간 확보
  while (qCount > 0) {
    OrbEvent e = queue[qHead];
    qHead = (qHead + 1) % QUEUE_MAX;
    qCount--;
    String msg;
    buildEventJson(msg, e);
    rs485SendLine(msg);
  }
  // 응답 끝 표시 겸 생존신호
  String hb = "{\"type\":\"heartbeat\",\"slave\":";
  hb += SLAVE_ID;
  hb += "}";
  rs485SendLine(hb);
}

// RS485 수신: "P<id>\n" 이 내 ID 면 응답. 그 외(JSON 에코 등)는 무시.
void pollRs485() {
  while (RS485.available()) {
    char c = (char)RS485.read();
    if (c == '\r') continue;
    if (c == '\n') {
      rs485In.trim();
      if (rs485In.length() > 0 && rs485In[0] == 'P') {
        int reqId = rs485In.substring(1).toInt();
        if (reqId == SLAVE_ID) respondToPoll();
      }
      rs485In = "";
      continue;
    }
    if (rs485In.length() < 64) rs485In += c;
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
  rs485In.reserve(80);
}

void loop() {
  // 1) 버스 스캔 → 변화 감지 → 링 갱신 + 큐 적재
  for (int i = 0; i < BUS_COUNT; i++) {
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

    // 스캔 중에도 master 폴 요청을 놓치지 않게 자주 확인
    pollRs485();
  }

  // 2) 폴 요청 처리 (delay 없이 촘촘히 → master 타임아웃 방지)
  pollRs485();
}
