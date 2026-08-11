#include <Adafruit_GFX.h>
#include <RGBmatrixPanel.h>

// ============================================================
// HUB75 핀
// ============================================================

#define CLK 8
#define OE 9
#define LAT 10

#define A A0
#define B A1
#define C A2

RGBmatrixPanel matrix(A, B, C, CLK, LAT, OE, false);


// ============================================================
// 설정
// ============================================================

// 0 ~ 7
// 실내 테스트는 1 권장
uint8_t BRIGHTNESS = 1;

int remainingTime = 99;

unsigned long previousMillis = 0;
const unsigned long COUNT_INTERVAL = 1000;


// ============================================================
// 색상
// ============================================================

uint16_t getTextColor() {
  return matrix.Color333(
    BRIGHTNESS,
    BRIGHTNESS,
    BRIGHTNESS);
}


// ============================================================
// 상단 그룹명
// ============================================================

void drawGroup() {
  uint16_t white = getTextColor();
  uint16_t black = matrix.Color333(0, 0, 0);

  matrix.setTextSize(1);
  matrix.setTextWrap(false);

  // 기본 5x7 폰트:
  // Aespa = 약 30px
  // 32px 화면에서 거의 중앙
  matrix.setCursor(1, 0);

  // foreground + background 같이 지정
  matrix.setTextColor(white, black);

  matrix.print("Aespa");
}


// ============================================================
// 하단 타이머
// ============================================================

void drawTimer() {
  uint16_t white = getTextColor();
  uint16_t black = matrix.Color333(0, 0, 0);

  char timerText[4];

  // 결과: 99", 60", 05", 00"
  snprintf(
    timerText,
    sizeof(timerText),
    "%02d\"",
    remainingTime);

  matrix.setTextSize(1);
  matrix.setTextWrap(false);

  // 3문자 × 6px = 약 18px
  // (32 - 18) / 2 = 7
  matrix.setCursor(7, 8);

  // 기존 숫자를 검정 배경으로 동시에 덮으므로
  // 화면 전체 clear가 필요 없음
  matrix.setTextColor(white, black);

  matrix.print(timerText);
}


// ============================================================
// 초기화
// ============================================================

void setup() {
  matrix.begin();

  // 최초 한 번만 전체 화면 초기화
  matrix.fillScreen(
    matrix.Color333(0, 0, 0));

  drawGroup();
  drawTimer();
}


// ============================================================
// 메인 루프
// ============================================================

void loop() {
  unsigned long now = millis();

  if (now - previousMillis >= COUNT_INTERVAL) {
    previousMillis += COUNT_INTERVAL;

    remainingTime--;

    if (remainingTime < 0) {
      remainingTime = 99;
    }

    drawTimer();
  }
}