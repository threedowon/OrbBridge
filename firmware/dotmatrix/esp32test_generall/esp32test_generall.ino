#include <Adafruit_GFX.h>
#include <Adafruit_Protomatter.h>

// ============================================================
// PANEL CONFIG
// 현재 P6 32x16 패널 기준
// ============================================================

#define MATRIX_WIDTH   32
#define MATRIX_HEIGHT  16

// Protomatter:
// 1~6 bit/channel
// 우선 최고값 6으로 테스트
#define BIT_DEPTH 6

// 너무 눈부시지 않도록 테스트 기본 밝기
// 0~255
uint8_t TEST_LEVEL = 64;

// White 단계
uint8_t WHITE_LOW  = 16;
uint8_t WHITE_MID  = 64;
uint8_t WHITE_HIGH = 128;


// ============================================================
// MATRIXPORTAL S3 HUB75 PINS
// Adafruit 공식 MatrixPortal S3 핀맵
// ============================================================

uint8_t rgbPins[] = {
  42, 41, 40,   // R1 G1 B1
  38, 39, 37    // R2 G2 B2
};

// MatrixPortal은 A~E까지 준비되어 있지만
// 32x16 패널은 A/B/C 세 개만 사용
uint8_t addrPins[] = {
  45, 36, 48
};

uint8_t clockPin = 2;
uint8_t latchPin = 47;
uint8_t oePin    = 14;


// ============================================================
// MATRIX OBJECT
//
// width = 32
// bit depth = 6
// parallel chains = 1
// address pins = 3 → 16 pixel high
// double buffer = false
// ============================================================

Adafruit_Protomatter matrix(
  MATRIX_WIDTH,
  BIT_DEPTH,
  1,
  rgbPins,
  3,
  addrPins,
  clockPin,
  latchPin,
  oePin,
  false
);


// ============================================================
// MATRIXPORTAL BUTTONS
// ============================================================

#define BUTTON_UP   6
#define BUTTON_DOWN 7


// ============================================================
// TEST CONTROL
// ============================================================

enum TestMode {
  TEST_BLACK = 0,
  TEST_RED,
  TEST_GREEN,
  TEST_BLUE,

  TEST_WHITE_LOW,
  TEST_WHITE_MID,
  TEST_WHITE_HIGH,

  TEST_RGB_BARS,
  TEST_WHITE_GRADIENT,
  TEST_RGB_GRADIENT,

  TEST_CHECKERBOARD,
  TEST_BORDER,
  TEST_CENTER_PIXEL,

  TEST_ROWS,
  TEST_COLUMNS,

  TEST_PIXEL_SCAN,

  TEST_COUNT
};

int currentTest = TEST_BLACK;

unsigned long lastButtonTime = 0;
const unsigned long BUTTON_DEBOUNCE = 180;

// 자동 순환
bool AUTO_CYCLE = false;
unsigned long lastAutoTime = 0;
const unsigned long AUTO_INTERVAL = 2500;


// pixel scan
int scanPixel = 0;
unsigned long lastScanTime = 0;
const unsigned long SCAN_INTERVAL = 60;


// ============================================================
// COLOR HELPERS
// ============================================================

uint16_t rgb(uint8_t r, uint8_t g, uint8_t b)
{
  return matrix.color565(r, g, b);
}

uint16_t black()
{
  return rgb(0, 0, 0);
}

uint16_t white(uint8_t level)
{
  return rgb(level, level, level);
}


// ============================================================
// CLEAR
// ============================================================

void clearMatrix()
{
  matrix.fillScreen(black());
}


// ============================================================
// TEST 0 : BLACK
// ============================================================

void testBlack()
{
  clearMatrix();
}


// ============================================================
// TEST 1 : RED
// ============================================================

void testRed()
{
  matrix.fillScreen(
    rgb(TEST_LEVEL, 0, 0)
  );
}


// ============================================================
// TEST 2 : GREEN
// ============================================================

void testGreen()
{
  matrix.fillScreen(
    rgb(0, TEST_LEVEL, 0)
  );
}


// ============================================================
// TEST 3 : BLUE
// ============================================================

void testBlue()
{
  matrix.fillScreen(
    rgb(0, 0, TEST_LEVEL)
  );
}


// ============================================================
// TEST 4~6 : WHITE LEVELS
// ============================================================

void testWhite(uint8_t level)
{
  matrix.fillScreen(
    white(level)
  );
}


// ============================================================
// TEST 7 : RGB BARS
//
// 화면을 R / G / B / WHITE 네 영역으로 나눔
// 색 채널 매핑 확인용
// ============================================================

void testRGBBars()
{
  clearMatrix();

  int quarter = MATRIX_WIDTH / 4;

  matrix.fillRect(
    0,
    0,
    quarter,
    MATRIX_HEIGHT,
    rgb(TEST_LEVEL, 0, 0)
  );

  matrix.fillRect(
    quarter,
    0,
    quarter,
    MATRIX_HEIGHT,
    rgb(0, TEST_LEVEL, 0)
  );

  matrix.fillRect(
    quarter * 2,
    0,
    quarter,
    MATRIX_HEIGHT,
    rgb(0, 0, TEST_LEVEL)
  );

  matrix.fillRect(
    quarter * 3,
    0,
    MATRIX_WIDTH - quarter * 3,
    MATRIX_HEIGHT,
    white(TEST_LEVEL)
  );
}


// ============================================================
// TEST 8 : WHITE GRADIENT
//
// 왼쪽 0 → 오른쪽 255
// bit depth / gamma / 계조 확인
// ============================================================

void testWhiteGradient()
{
  clearMatrix();

  for (int x = 0; x < MATRIX_WIDTH; x++)
  {
    uint8_t v =
      map(x, 0, MATRIX_WIDTH - 1, 0, 255);

    matrix.drawFastVLine(
      x,
      0,
      MATRIX_HEIGHT,
      white(v)
    );
  }
}


// ============================================================
// TEST 9 : RGB GRADIENT
//
// 위쪽 4행 = RED
// 다음 4행 = GREEN
// 다음 4행 = BLUE
// 아래 4행 = WHITE
// ============================================================

void testRGBGradient()
{
  clearMatrix();

  for (int x = 0; x < MATRIX_WIDTH; x++)
  {
    uint8_t v =
      map(x, 0, MATRIX_WIDTH - 1, 0, 255);

    matrix.drawFastVLine(
      x, 0, 4,
      rgb(v, 0, 0)
    );

    matrix.drawFastVLine(
      x, 4, 4,
      rgb(0, v, 0)
    );

    matrix.drawFastVLine(
      x, 8, 4,
      rgb(0, 0, v)
    );

    matrix.drawFastVLine(
      x, 12, 4,
      rgb(v, v, v)
    );
  }
}


// ============================================================
// TEST 10 : CHECKERBOARD
//
// 픽셀 위치 / ghost / scan 문제 확인
// ============================================================

void testCheckerboard()
{
  clearMatrix();

  for (int y = 0; y < MATRIX_HEIGHT; y++)
  {
    for (int x = 0; x < MATRIX_WIDTH; x++)
    {
      if ((x + y) & 1)
      {
        matrix.drawPixel(
          x,
          y,
          white(TEST_LEVEL)
        );
      }
    }
  }
}


// ============================================================
// TEST 11 : BORDER
//
// 물리적 화면 끝 / addressing 확인
// ============================================================

void testBorder()
{
  clearMatrix();

  matrix.drawRect(
    0,
    0,
    MATRIX_WIDTH,
    MATRIX_HEIGHT,
    white(TEST_LEVEL)
  );

  // 네 모서리를 다른 색으로
  matrix.drawPixel(
    0,
    0,
    rgb(255, 0, 0)
  );

  matrix.drawPixel(
    MATRIX_WIDTH - 1,
    0,
    rgb(0, 255, 0)
  );

  matrix.drawPixel(
    0,
    MATRIX_HEIGHT - 1,
    rgb(0, 0, 255)
  );

  matrix.drawPixel(
    MATRIX_WIDTH - 1,
    MATRIX_HEIGHT - 1,
    rgb(255, 255, 255)
  );
}


// ============================================================
// TEST 12 : CENTER RED PIXEL
//
// 요청했던 딱 한 픽셀 중앙 빨강
//
// 32x16은 정확한 단일 중앙 픽셀이 없으므로
// 좌상단 기준 중앙에 가까운 (16,8)을 사용
// ============================================================

void testCenterPixel()
{
  clearMatrix();

  const int cx = MATRIX_WIDTH / 2;
  const int cy = MATRIX_HEIGHT / 2;

  matrix.drawPixel(
    cx,
    cy,
    rgb(255, 0, 0)
  );
}


// ============================================================
// TEST 13 : ROW TEST
//
// 행마다 색을 번갈아 표시.
// A/B/C address mapping 문제 확인용.
// ============================================================

void testRows()
{
  clearMatrix();

  for (int y = 0; y < MATRIX_HEIGHT; y++)
  {
    uint16_t color;

    switch (y % 4)
    {
      case 0:
        color = rgb(TEST_LEVEL, 0, 0);
        break;

      case 1:
        color = rgb(0, TEST_LEVEL, 0);
        break;

      case 2:
        color = rgb(0, 0, TEST_LEVEL);
        break;

      default:
        color = white(TEST_LEVEL);
        break;
    }

    matrix.drawFastHLine(
      0,
      y,
      MATRIX_WIDTH,
      color
    );
  }
}


// ============================================================
// TEST 14 : COLUMN TEST
// ============================================================

void testColumns()
{
  clearMatrix();

  for (int x = 0; x < MATRIX_WIDTH; x++)
  {
    uint16_t color;

    switch (x % 4)
    {
      case 0:
        color = rgb(TEST_LEVEL, 0, 0);
        break;

      case 1:
        color = rgb(0, TEST_LEVEL, 0);
        break;

      case 2:
        color = rgb(0, 0, TEST_LEVEL);
        break;

      default:
        color = white(TEST_LEVEL);
        break;
    }

    matrix.drawFastVLine(
      x,
      0,
      MATRIX_HEIGHT,
      color
    );
  }
}


// ============================================================
// STATIC TEST SELECT
// ============================================================

void drawTest(int test)
{
  clearMatrix();

  Serial.print("TEST ");
  Serial.print(test);
  Serial.print(" / ");
  Serial.println(TEST_COUNT - 1);

  switch (test)
  {
    case TEST_BLACK:
      Serial.println("BLACK");
      testBlack();
      break;

    case TEST_RED:
      Serial.println("SOLID RED");
      testRed();
      break;

    case TEST_GREEN:
      Serial.println("SOLID GREEN");
      testGreen();
      break;

    case TEST_BLUE:
      Serial.println("SOLID BLUE");
      testBlue();
      break;

    case TEST_WHITE_LOW:
      Serial.println("WHITE LOW");
      testWhite(WHITE_LOW);
      break;

    case TEST_WHITE_MID:
      Serial.println("WHITE MID");
      testWhite(WHITE_MID);
      break;

    case TEST_WHITE_HIGH:
      Serial.println("WHITE HIGH");
      testWhite(WHITE_HIGH);
      break;

    case TEST_RGB_BARS:
      Serial.println("RGB + WHITE BARS");
      testRGBBars();
      break;

    case TEST_WHITE_GRADIENT:
      Serial.println("WHITE GRADIENT");
      testWhiteGradient();
      break;

    case TEST_RGB_GRADIENT:
      Serial.println("RGB GRADIENT");
      testRGBGradient();
      break;

    case TEST_CHECKERBOARD:
      Serial.println("CHECKERBOARD");
      testCheckerboard();
      break;

    case TEST_BORDER:
      Serial.println("BORDER + CORNERS");
      testBorder();
      break;

    case TEST_CENTER_PIXEL:
      Serial.println("CENTER RED PIXEL");
      testCenterPixel();
      break;

    case TEST_ROWS:
      Serial.println("ROW TEST");
      testRows();
      break;

    case TEST_COLUMNS:
      Serial.println("COLUMN TEST");
      testColumns();
      break;

    case TEST_PIXEL_SCAN:
      Serial.println("PIXEL SCAN");
      scanPixel = 0;
      break;
  }

  matrix.show();
}


// ============================================================
// PIXEL SCAN
//
// 한 픽셀씩 32x16 전부 순회.
// 물리 좌표와 소프트웨어 좌표가 일치하는지 가장 확실한 테스트.
// ============================================================

void updatePixelScan()
{
  unsigned long now = millis();

  if (now - lastScanTime < SCAN_INTERVAL)
    return;

  lastScanTime = now;

  clearMatrix();

  int x = scanPixel % MATRIX_WIDTH;
  int y = scanPixel / MATRIX_WIDTH;

  matrix.drawPixel(
    x,
    y,
    rgb(255, 0, 0)
  );

  matrix.show();

  Serial.print("PIXEL ");
  Serial.print(x);
  Serial.print(", ");
  Serial.println(y);

  scanPixel++;

  if (scanPixel >= MATRIX_WIDTH * MATRIX_HEIGHT)
  {
    scanPixel = 0;
  }
}


// ============================================================
// NEXT / PREVIOUS
// ============================================================

void nextTest()
{
  currentTest++;

  if (currentTest >= TEST_COUNT)
    currentTest = 0;

  drawTest(currentTest);
}

void previousTest()
{
  currentTest--;

  if (currentTest < 0)
    currentTest = TEST_COUNT - 1;

  drawTest(currentTest);
}


// ============================================================
// SERIAL CONTROL
//
// n = next
// p = previous
//
// 0~9 = 직접 테스트 0~9
//
// a = auto cycle ON/OFF
// ============================================================

void handleSerial()
{
  if (!Serial.available())
    return;

  char c = Serial.read();

  if (c == 'n' || c == 'N')
  {
    nextTest();
  }

  else if (c == 'p' || c == 'P')
  {
    previousTest();
  }

  else if (c == 'a' || c == 'A')
  {
    AUTO_CYCLE = !AUTO_CYCLE;

    Serial.print("AUTO_CYCLE = ");
    Serial.println(AUTO_CYCLE ? "ON" : "OFF");
  }

  else if (c >= '0' && c <= '9')
  {
    int test = c - '0';

    if (test < TEST_COUNT)
    {
      currentTest = test;
      drawTest(currentTest);
    }
  }
}


// ============================================================
// BUTTON CONTROL
//
// MatrixPortal S3
// UP   = next
// DOWN = previous
// ============================================================

void handleButtons()
{
  unsigned long now = millis();

  if (now - lastButtonTime < BUTTON_DEBOUNCE)
    return;

  if (digitalRead(BUTTON_UP) == LOW)
  {
    lastButtonTime = now;
    nextTest();
  }

  else if (digitalRead(BUTTON_DOWN) == LOW)
  {
    lastButtonTime = now;
    previousTest();
  }
}


// ============================================================
// SETUP
// ============================================================

void setup()
{
  Serial.begin(115200);

  delay(500);

  Serial.println();
  Serial.println("==============================");
  Serial.println("MatrixPortal S3");
  Serial.println("32x16 HUB75 test rig");
  Serial.println("==============================");

  pinMode(BUTTON_UP, INPUT_PULLUP);
  pinMode(BUTTON_DOWN, INPUT_PULLUP);

  ProtomatterStatus status = matrix.begin();

  Serial.print("Protomatter begin status: ");
  Serial.println((int)status);

  if (status != PROTOMATTER_OK)
  {
    Serial.println("ERROR: matrix.begin() failed");

    while (true)
    {
      delay(100);
    }
  }

  Serial.print("Matrix size: ");
  Serial.print(matrix.width());
  Serial.print(" x ");
  Serial.println(matrix.height());

  Serial.print("Bit depth: ");
  Serial.println(BIT_DEPTH);

  Serial.println();
  Serial.println("Controls:");
  Serial.println("UP button   : next test");
  Serial.println("DOWN button : previous test");
  Serial.println("Serial n    : next");
  Serial.println("Serial p    : previous");
  Serial.println("Serial a    : auto cycle");
  Serial.println();

  drawTest(currentTest);
}


// ============================================================
// LOOP
// ============================================================

void loop()
{
  handleSerial();
  handleButtons();

  // 움직이는 단일 픽셀 테스트
  if (currentTest == TEST_PIXEL_SCAN)
  {
    updatePixelScan();
  }

  // 자동 테스트 순환
  if (AUTO_CYCLE)
  {
    unsigned long now = millis();

    if (now - lastAutoTime >= AUTO_INTERVAL)
    {
      lastAutoTime = now;
      nextTest();
    }
  }

  // refresh 확인
  static unsigned long lastFPS = 0;

  if (millis() - lastFPS >= 3000)
  {
    lastFPS = millis();

    Serial.print("Refresh FPS ~= ");
    Serial.println(matrix.getFrameCount());
  }
}