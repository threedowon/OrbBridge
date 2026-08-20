// ArduinoJson과 ESP32-HUB75-MatrixPanel-I2S-DMA 라이브러리를 사용한다.
#include <ArduinoJson.h>
#include <ESP32-HUB75-MatrixPanel-I2S-DMA.h>

#define PANEL_WIDTH 64
#define PANEL_HEIGHT 32
#define PANEL_CHAIN 3
#define UP_BUTTON_PIN 6

HUB75_I2S_CFG::i2s_pins pins = {
  42, 41, 40, 38, 39, 37,
  45, 36, 48, 35, -1,
  47, 14, 2
};

HUB75_I2S_CFG mxconfig(PANEL_WIDTH, PANEL_HEIGHT, PANEL_CHAIN, pins);
MatrixPanel_I2S_DMA *display = nullptr;
String serialLine;
String lastText;

const int CHAR_W = 6;
const int DISPLAY_WIDTH = PANEL_WIDTH * PANEL_CHAIN;
int lastSeconds = -1;
int viewMode = 0;
bool buttonPressed = false;
bool hasContent = false;
uint32_t lastButtonTick = 0;

void drawName(const char *text) {
  display->fillRect(0, 0, 128, PANEL_HEIGHT, 0);
  display->setTextColor(display->color565(255, 255, 255));
  display->setTextSize(1);
  display->setCursor(4, 12);
  display->print(text);
}

void drawCountdown(int seconds) {
  display->fillRect(128, 0, 64, PANEL_HEIGHT, 0);
  display->setTextColor(display->color565(255, 255, 255));
  display->setTextSize(1);

  char text[8];
  snprintf(text, sizeof(text), "%d\"", seconds);
  int x = DISPLAY_WIDTH - strlen(text) * CHAR_W - 4;
  display->setCursor(x, 12);
  display->print(text);
}

void drawPanelInfo() {
  display->clearScreen();
  display->setTextColor(display->color565(255, 255, 255));
  display->setTextSize(1);
  display->setTextWrap(false);

  for (int panel = 0; panel < PANEL_CHAIN; panel++) {
    int x = panel * PANEL_WIDTH + 2;
    display->setCursor(x, 2);
    display->printf("PANEL %d", panel + 1);
    display->setCursor(x, 12);
    display->printf("BIT %d", mxconfig.getPixelColorDepthBits());
    display->setCursor(x, 22);
    display->printf("%d Hz", display->calculated_refresh_rate);
  }
}

void drawRuntime() {
  display->clearScreen();
  if (!hasContent) return;
  drawName(lastText.c_str());
  drawCountdown(lastSeconds);
}

void changeView() {
  viewMode = (viewMode + 1) % 3;
  if (viewMode == 1) {
    drawPanelInfo();
  } else if (viewMode == 2) {
    display->fillScreen(display->color565(255, 255, 255));
  } else {
    drawRuntime();
  }
}

void handleCommand(const String &line) {
  StaticJsonDocument<128> message;
  if (deserializeJson(message, line)) return;

  const char *type = message["type"] | "";
  if (!strcmp(type, "clear")) {
    hasContent = false;
    if (viewMode == 0) display->clearScreen();
    return;
  }
  if (strcmp(type, "display")) return;

  const char *text = message["text"] | "";
  int seconds = message["seconds"] | -1;
  if (seconds < 0) return;

  lastText = text;
  lastSeconds = seconds;
  hasContent = true;
  if (viewMode == 0) drawRuntime();
}

void setup() {
  Serial.begin(115200);
  delay(1500);

  mxconfig.setPixelColorDepthBits(4);
  mxconfig.i2sspeed = HUB75_I2S_CFG::HZ_20M;
  display = new MatrixPanel_I2S_DMA(mxconfig);
  if (!display->begin()) {
    Serial.println("Display begin() FAILED");
    while (true) delay(1000);
  }

  display->setBrightness8(255);
  display->clearScreen();
  serialLine.reserve(128);
  lastText.reserve(40);
  pinMode(UP_BUTTON_PIN, INPUT_PULLUP);
}

void loop() {
  bool pressed = digitalRead(UP_BUTTON_PIN) == LOW;
  uint32_t now = millis();
  if (pressed && !buttonPressed && now - lastButtonTick >= 200) {
    lastButtonTick = now;
    changeView();
  }
  buttonPressed = pressed;

  while (Serial.available()) {
    char value = Serial.read();
    if (value == '\n') {
      handleCommand(serialLine);
      serialLine = "";
    } else if (value != '\r') {
      serialLine += value;
      if (serialLine.length() > 160) serialLine = "";
    }
  }
}
