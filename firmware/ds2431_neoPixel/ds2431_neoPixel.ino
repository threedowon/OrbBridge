#include <Adafruit_NeoPixel.h>
#include <OneWire.h>

// =========================
// PIN 설정
// =========================
constexpr uint8_t CONTROL_PIN = 38;  // LED DATA
constexpr uint8_t ONEWIRE_PIN = 11;  // DS2431 DQ ← 실제 핀으로 변경

constexpr uint16_t LED_COUNT = 10;

// =========================
// LED
// =========================
Adafruit_NeoPixel pixels(
  LED_COUNT,
  CONTROL_PIN,
  NEO_GRB + NEO_KHZ800
);

// =========================
// DS2431 / 1-Wire
// =========================
OneWire oneWire(ONEWIRE_PIN);

bool findDS2431() {
  uint8_t addr[8];

  oneWire.reset_search();

  while (oneWire.search(addr)) {

    // CRC 검사
    if (OneWire::crc8(addr, 7) != addr[7]) {
      continue;
    }

    // DS2431 Family Code = 0x2D
    if (addr[0] == 0x2D) {
      return true;
    }
  }

  return false;
}

void ledOn() {
  for (uint16_t i = 0; i < LED_COUNT; i++) {
    pixels.setPixelColor(
      i,
      pixels.Color(255, 255, 255)
    );
  }

  pixels.show();
}

void ledOff() {
  pixels.clear();
  pixels.show();
}

void setup() {
  Serial.begin(115200);

  pixels.begin();
  pixels.setBrightness(50);
  pixels.clear();
  pixels.show();

  Serial.println("DS2431 detection start");
}

void loop() {

  if (findDS2431()) {
    Serial.println("DS2431 detected");
    ledOn();
  }
  else {
    Serial.println("DS2431 not detected");
    ledOff();
  }

  delay(500);
}