#include <ESP32-HUB75-MatrixPanel-I2S-DMA.h>

#define PANEL_RES_X 32
#define PANEL_RES_Y 16
#define PANEL_CHAIN 1

#define R1_PIN  42
#define G1_PIN  41
#define B1_PIN  40
#define R2_PIN  38
#define G2_PIN  39
#define B2_PIN  37

#define A_PIN   45
#define B_PIN   36
#define C_PIN   48
#define D_PIN   35
#define E_PIN   21

#define LAT_PIN 47
#define OE_PIN  14
#define CLK_PIN 2

MatrixPanel_I2S_DMA *display = nullptr;

const int STEP_MS = 300;

void printConfig() {
  Serial.println();
  Serial.println("=== HUB75 CONFIG ===");

  Serial.print("Resolution: ");
  Serial.print(PANEL_RES_X);
  Serial.print(" x ");
  Serial.println(PANEL_RES_Y);

  Serial.print("Color depth: ");
  Serial.print(display->getCfg().getPixelColorDepthBits());
  Serial.println(" bit/channel");

  Serial.print("Panel refresh: ");
  Serial.print(display->calculated_refresh_rate);
  Serial.println(" Hz");

  Serial.println("====================");
}

void setup() {
  Serial.begin(115200);

  // Serial Monitor 연결 약간 대기
  unsigned long start = millis();
  while (!Serial && millis() - start < 5000) {
    delay(10);
  }

  HUB75_I2S_CFG::i2s_pins pins = {
    R1_PIN, G1_PIN, B1_PIN,
    R2_PIN, G2_PIN, B2_PIN,
    A_PIN, B_PIN, C_PIN, D_PIN, E_PIN,
    LAT_PIN, OE_PIN, CLK_PIN
  };

  HUB75_I2S_CFG mxconfig(
    PANEL_RES_X,
    PANEL_RES_Y,
    PANEL_CHAIN,
    pins
  );

  mxconfig.setPixelColorDepthBits(8);
  mxconfig.min_refresh_rate = 60;

  display = new MatrixPanel_I2S_DMA(mxconfig);

  if (!display->begin()) {
    Serial.println("DMA init failed");
    while (1) delay(1000);
  }

  display->setBrightness8(255);
  display->fillScreenRGB888(0, 0, 0);

  printConfig();
}

void loop() {

  // Serial Monitor를 늦게 열어도 확인 가능
  printConfig();

  // 0 → 255, 정확히 1 LSB씩
  for (int v = 0; v <= 255; v++) {

    display->fillScreenRGB888(v, v, v);

    Serial.print("RGB = ");
    Serial.println(v);

    delay(STEP_MS);
  }

  delay(1000);

  display->fillScreenRGB888(0, 0, 0);
  delay(1000);
}