#include <Adafruit_GFX.h>
#include <Adafruit_Protomatter.h>

#define WIDTH 64
#define BIT_DEPTH 6

uint8_t rgbPins[] = {
  42, 41, 40,
  38, 39, 37
};

uint8_t addrPins[] = {
  45, 36, 48, 35   // A B C D
};

uint8_t clockPin = 2;
uint8_t latchPin = 47;
uint8_t oePin    = 14;

Adafruit_Protomatter matrix(
  WIDTH,
  BIT_DEPTH,
  1,
  rgbPins,
  4,
  addrPins,
  clockPin,
  latchPin,
  oePin,
  false
);

void setup() {
  ProtomatterStatus status = matrix.begin();

  if (status != PROTOMATTER_OK) {
    while (1);
  }

  // 전체 최대 흰색
  matrix.fillScreen(
    matrix.color565(255, 255, 255)
  );

  matrix.show();
}

void loop() {
  // 계속 켜진 상태 유지
}