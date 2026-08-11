#include <Adafruit_GFX.h>
#include <RGBmatrixPanel.h>

#define CLK 8
#define OE  9
#define LAT 10

#define A   A0
#define B   A1
#define C   A2

RGBmatrixPanel matrix(A, B, C, CLK, LAT, OE, false);

void setup() {
  matrix.begin();
}

void loop() {
/*
  // 빨강
  matrix.fillScreen(matrix.Color333(7, 0, 0));
  delay(1000);

  // 초록
  matrix.fillScreen(matrix.Color333(0, 7, 0));
  delay(1000);

  // 파랑
  matrix.fillScreen(matrix.Color333(0, 0, 7));
  delay(1000);

    // 끄기
  matrix.fillScreen(0);
  delay(1000);

*/
  // 흰색
  matrix.fillScreen(matrix.Color333(7, 7, 7));
  delay(1000);


}