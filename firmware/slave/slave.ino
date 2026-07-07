/*
 * OrbBridge — Slave ESP32-S3
 *
 * 16× OneWire → DS2431
 * RS485 → Master (added / removed JSON)
 * NeoPixel 링 16개 (각 12 LED) → GPIO 38 체인, DS2431 1개당 링 1개
 *
 * RS485 핀·속도: 실물 테스트와 동일 (TX=20, RX=21, 9600)
 */

#include <OneWire.h>
#include <Adafruit_NeoPixel.h>
#include <HardwareSerial.h>

#define TX_PIN       20
#define RX_PIN       21
#define RGB_PIN      48
#define LED_BRIGHTNESS 32   // 255의 1/8
#define RS485_BAUD   9600
#define SLAVE_ID     1   // 보드 추가 시 2, 3…

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

// 버스 i → 링 i (LED [i*12 … i*12+11]). 전체 링은 GPIO 45 한 선에 직렬 연결.
Adafruit_NeoPixel ring(BUS_COUNT * LEDS_PER_RING, RING_PIN, NEO_GRB + NEO_KHZ800);

byte lastAddr[BUS_COUNT][8];
bool hasLast[BUS_COUNT] = {false};

void rs485SendLine(const String& line) {
  RS485.println(line);
  RS485.flush();
#if DEBUG_USB
  Serial.println(line);
#endif
}

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
  rs485SendLine(msg);
}

void sendRemoved(int busIndex, const byte addr[8]) {
  sendEvent("removed", busIndex, addr);
  setRing(busIndex, 0);   // 링 소등
}

void sendAdded(int busIndex, const byte addr[8]) {
  sendEvent("added", busIndex, addr);
  setRing(busIndex, ring.Color(255, 147, 41));   // 감지 → 따뜻한 노란색 점등
  flashRed();
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
}

void loop() {
  for (int i = 0; i < BUS_COUNT; i++) {
    byte addr[8];
    bool present = readBus(i, addr);

    if (present) {
      if (!hasLast[i]) {
        memcpy(lastAddr[i], addr, 8);
        hasLast[i] = true;
        sendAdded(i, addr);
      } else if (!sameAddr(lastAddr[i], addr)) {
        sendRemoved(i, lastAddr[i]);
        memcpy(lastAddr[i], addr, 8);
        sendAdded(i, addr);
      }
    } else {
      if (hasLast[i]) {
        sendRemoved(i, lastAddr[i]);
        hasLast[i] = false;
        memset(lastAddr[i], 0, 8);
      }
    }
  }
  delay(100);
}
