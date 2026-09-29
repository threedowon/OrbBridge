// ArduinoJson과 ESP32-HUB75-MatrixPanel-I2S-DMA 라이브러리를 사용한다.
#include <ArduinoJson.h>
#include <ESP32-HUB75-MatrixPanel-I2S-DMA.h>

#define PANEL_WIDTH 64
#define PANEL_HEIGHT 32
#define PANEL_CHAIN 3

HUB75_I2S_CFG::i2s_pins pins = {
  42, 41, 40, 38, 39, 37,
  45, 36, 48, 35, -1,
  47, 14, 2
};

HUB75_I2S_CFG mxconfig(PANEL_WIDTH, PANEL_HEIGHT, PANEL_CHAIN, pins);
MatrixPanel_I2S_DMA *display = nullptr;
String serialLine;
String lastCommand;
bool droppingLine = false;
uint32_t lastSerialByteAt = 0;
uint32_t lastValidCommandAt = 0;
bool linkTimedOut = false;

const int CHAR_W = 6;
const int DISPLAY_WIDTH = PANEL_WIDTH * PANEL_CHAIN;

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

  char text[16];
  snprintf(text, sizeof(text), "%d\"", seconds);
  int x = DISPLAY_WIDTH - strlen(text) * CHAR_W - 4;
  display->setCursor(x, 12);
  display->print(text);
}

void drawStatus(const char *raw) {
  String text(raw);
  text.toUpperCase();
  const int size = 1;
  text = text.substring(0, DISPLAY_WIDTH / (CHAR_W * size));
  int width = text.length() ? (text.length() * CHAR_W - 1) * size : 0;
  display->clearScreen();
  display->setTextWrap(false);
  display->setTextSize(size);
  display->setTextColor(display->color565(255, 255, 255));
  display->setCursor((DISPLAY_WIDTH - width) / 2, (PANEL_HEIGHT - 7 * size) / 2);
  display->print(text);
}

void handleCommand(const String &line) {
  StaticJsonDocument<256> message;
  if (deserializeJson(message, line)) return;
  const char *type = message["type"] | "";
  if (strcmp(type, "clear") && strcmp(type, "status") && strcmp(type, "display")) return;
  if (strcmp(type, "clear") && !message["text"].is<const char *>()) return;
  if (!strcmp(type, "display") &&
      (!message["seconds"].is<int>() || message["seconds"].as<int>() < 0)) return;
  // Host heartbeat recovers a reboot without repeatedly clearing a healthy panel.
  lastValidCommandAt = millis();
  linkTimedOut = false;
  if (line == lastCommand) return;
  lastCommand = line;
  if (!strcmp(type, "clear")) {
    display->clearScreen();
  } else if (!strcmp(type, "status")) {
    drawStatus(message["text"].as<const char *>());
  } else {
    display->clearScreen();
    drawName(message["text"].as<const char *>());
    drawCountdown(message["seconds"].as<int>());
  }
  // Draw the complete frame offscreen, then swap once; no visible blank frame.
  display->flipDMABuffer();
}

void setup() {
  Serial.begin(115200);
  delay(1500);

  mxconfig.setPixelColorDepthBits(2);
  mxconfig.double_buff = true;
  mxconfig.i2sspeed = HUB75_I2S_CFG::HZ_20M;
  display = new MatrixPanel_I2S_DMA(mxconfig);
  if (!display->begin()) {
    Serial.println("Display begin() FAILED");
    while (true) delay(1000);
  }

  display->setBrightness8(10);
  display->clearScreen();
  display->setTextWrap(false);
  serialLine.reserve(160);
  lastCommand.reserve(160);
  lastValidCommandAt = millis();
}

void loop() {
  // A partial/oversized frame must never be interpreted as a new command tail.
  if ((serialLine.length() || droppingLine) && millis() - lastSerialByteAt > 250) {
    serialLine = "";
    droppingLine = false;
  }
  int budget = 256;
  while (Serial.available() && budget-- > 0) {
    char value = Serial.read();
    lastSerialByteAt = millis();
    if (value == '\n') {
      if (!droppingLine) handleCommand(serialLine);
      serialLine = "";
      droppingLine = false;
    } else if (value != '\r' && !droppingLine) {
      if (serialLine.length() >= 160) {
        serialLine = "";
        droppingLine = true;
      } else {
        serialLine += value;
      }
    }
  }
  if (!linkTimedOut && millis() - lastValidCommandAt >= 5000) {
    linkTimedOut = true;
    lastCommand = "";
    drawStatus("OFFLINE");
    display->flipDMABuffer();
  }
}
