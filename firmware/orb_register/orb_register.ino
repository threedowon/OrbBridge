/*
 * OrbBridge — 오브 등록기 (ESP32-S3)
 *
 * 16× OneWire → DS2431. USB Serial(115200) → PC 등록 도구(register_tool.py).
 * DS2431 오브를 셀에 올리면 한 번만 JSON 출력 + 온보드 RGB 빨강 깜빡.
 *   출력 형식: {"type":"ds2431","cell":n,"esp_gpio":g,"id":"AA:BB:CC:..."}
 *
 * 마스터/RS485 없이 이 보드만 PC에 USB로 연결해서 orb ID를 등록한다.
 * register_core.py의 parse_serial_line 이 type=ds2431 + id 필드를 읽는다.
 *
 * 핀 배치는 slave.ino 와 동일. 매 스캔마다 reset_search() 하므로
 * 오브를 올린 순간 1회만 출력되고, 가만히 있으면 조용하다(등록에 적합).
 */

#include <OneWire.h>
#include <Adafruit_NeoPixel.h>

#define RGB_PIN        48
#define LED_BRIGHTNESS 32   // 255의 1/8

Adafruit_NeoPixel led(1, RGB_PIN, NEO_GRB + NEO_KHZ800);

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

byte lastAddr[BUS_COUNT][8];
bool hasLast[BUS_COUNT] = {false};

void flashRed() {
  led.setPixelColor(0, led.Color(255, 0, 0));
  led.show();
  delay(80);
  led.clear();
  led.show();
}

bool sameAddr(const byte a[8], const byte b[8]) {
  for (int i = 0; i < 8; i++) {
    if (a[i] != b[i]) return false;
  }
  return true;
}

void printAddr(const byte addr[8]) {
  for (int i = 0; i < 8; i++) {
    if (i > 0) Serial.print(":");
    if (addr[i] < 16) Serial.print("0");
    Serial.print(addr[i], HEX);
  }
}

void sendOrbId(int index, const byte addr[8]) {
  Serial.print("{\"type\":\"ds2431\",\"cell\":");
  Serial.print(index + 1);
  Serial.print(",\"esp_gpio\":");
  Serial.print(busPins[index]);
  Serial.print(",\"id\":\"");
  printAddr(addr);
  Serial.println("\"}");
}

// 버스 i에 오브가 있으면 addr을 채우고 true를 반환한다.
// 매번 reset_search()를 호출해 첫 장치부터 탐색한다.
bool readBus(int i, byte addr[8]) {
  buses[i].reset_search();
  if (!buses[i].search(addr)) {
    return false;
  }
  if (OneWire::crc8(addr, 7) != addr[7]) {
    Serial.print("CRC error at cell ");
    Serial.println(i + 1);
    return false;
  }
  return true;
}

void setup() {
  Serial.begin(115200);
  delay(2000);

  led.begin();
  led.setBrightness(LED_BRIGHTNESS);
  led.clear();
  led.show();

  Serial.println("Orb Register — DS2431 16 GPIO scan start");
}

void loop() {
  for (int i = 0; i < BUS_COUNT; i++) {
    byte addr[8];

    if (readBus(i, addr)) {
      // 처음 감지됐거나, 다른 DS2431 로 바뀐 경우에만 1회 출력
      if (!hasLast[i] || !sameAddr(addr, lastAddr[i])) {
        memcpy(lastAddr[i], addr, 8);
        hasLast[i] = true;
        sendOrbId(i, addr);
        flashRed();
      }
    } else {
      // 오브가 없으면 상태 초기화 → 다시 올리면 재감지·재출력
      hasLast[i] = false;
    }
  }

  delay(100);
}
