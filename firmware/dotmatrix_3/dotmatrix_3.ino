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
String lastCommand;
bool droppingLine = false;
uint32_t lastSerialByteAt = 0;
uint32_t lastValidCommandAt = 0;
bool linkTimedOut = false;
const int VIEW_MODE_COUNT = 7;
int viewMode = 0;
bool viewDirty = false;
bool buttonRaw = false;
bool buttonStable = false;
uint32_t buttonChangedAt = 0;

// ===== 사용자 설정 변수 =====
const int PADDING_TOP    = 12;  // 상단 여백(px) — 텍스트 시작 y좌표
const int PADDING_BOTTOM = 0;   // 하단 여백(px)
const int PADDING_LEFT   = 16;   // 좌측 여백(px) — 이름 시작 x좌표
const int PADDING_RIGHT  = 16;   // 우측 여백(px) — 카운트다운 우측 끝 여백
const int FONT_SIZE      = 1;   // 폰트 크기 배율 (1 = 6x8px 기본 폰트)

// ============================

const int CHAR_W = 6;  // 기본 폰트 1글자 폭(배율 1 기준)
const int DISPLAY_WIDTH = PANEL_WIDTH * PANEL_CHAIN;


// 카운트다운 최대 폭 기준 경계 (3자리 숫자 + " = 4글자)
const int COUNTDOWN_MAX_CHARS = 4;
const int NAME_REGION_END = DISPLAY_WIDTH - COUNTDOWN_MAX_CHARS * CHAR_W * FONT_SIZE - PADDING_RIGHT;
// 192 - 24 - 14 = 154

// "Aespa-Karina", "Aespa -Karina" 등을 "Aespa - Karina"로 정규화한다.
String formatName(const String &raw) {
  String out;
  out.reserve(raw.length() + 4);
  for (unsigned int i = 0; i < raw.length(); i++) {
    char c = raw[i];
    if (c == '-') {
      // 하이픈 앞 공백 정리 후 " - " 삽입
      while (out.length() > 0 && out[out.length() - 1] == ' ') {
        out.remove(out.length() - 1);
      }
      out += " - ";
      // 하이픈 뒤 공백 스킵
      while (i + 1 < raw.length() && raw[i + 1] == ' ') i++;
    } else {
      out += c;
    }
  }
  return out;
}

void drawName(const char *text) {
  display->fillRect(0, 0, NAME_REGION_END, PANEL_HEIGHT, 0);
  display->setTextColor(display->color565(255, 255, 255));
  display->setTextSize(FONT_SIZE);
  display->setCursor(PADDING_LEFT, PADDING_TOP);

  // 경계를 넘지 않도록 최대 글자 수로 잘라서 출력
  int maxChars = (NAME_REGION_END - PADDING_LEFT) / (CHAR_W * FONT_SIZE);
  char buf[40];
  maxChars = min(maxChars, (int)sizeof(buf) - 1);
  strncpy(buf, text, maxChars);
  buf[maxChars] = '\0';
  display->print(buf);
}


void drawCountdown(int seconds) {
  display->fillRect(NAME_REGION_END, 0, DISPLAY_WIDTH - NAME_REGION_END, PANEL_HEIGHT, 0);
  display->setTextColor(display->color565(255, 255, 255));
  display->setTextSize(FONT_SIZE);

  char text[16];
  snprintf(text, sizeof(text), "%d\"", seconds);
  int x = DISPLAY_WIDTH - strlen(text) * CHAR_W * FONT_SIZE - PADDING_RIGHT;
  display->setCursor(x, PADDING_TOP);
  display->print(text);
}

void drawStatus(const char *raw) {
  String text(raw);
  text.toUpperCase();
  const int size = FONT_SIZE;
  text = text.substring(0, DISPLAY_WIDTH / (CHAR_W * size));
  int width = text.length() ? (text.length() * CHAR_W - 1) * size : 0;
  display->clearScreen();
  display->setTextWrap(false);
  display->setTextSize(size);
  display->setTextColor(display->color565(255, 255, 255));
  display->setCursor((DISPLAY_WIDTH - width) / 2, (PANEL_HEIGHT - 7 * size) / 2);
  display->print(text);
}

void drawRuntime() {
  display->clearScreen();
  if (linkTimedOut) {
    drawStatus("OFFLINE");
    return;
  }
  StaticJsonDocument<256> message;
  if (deserializeJson(message, lastCommand)) return;
  const char *type = message["type"] | "";
  if (!strcmp(type, "status")) {
    drawStatus(message["text"].as<const char *>());
  } else if (!strcmp(type, "display")) {
    String text = formatName(message["text"].as<const char *>());
    drawName(text.c_str());
    drawCountdown(message["seconds"].as<int>());
  }
}

void drawView() {
  display->clearScreen();
  display->setTextWrap(false);
  display->setTextSize(1);
  display->setTextColor(display->color565(255, 255, 255));
  if (viewMode == 0) {
    drawRuntime();
  } else if (viewMode == 1) {
    for (int panel = 0; panel < PANEL_CHAIN; panel++) {
      int x = panel * PANEL_WIDTH + 2;
      display->setCursor(x, 2);
      display->printf("PANEL %d", panel + 1);
      display->setCursor(x, 12);
      display->printf("BIT %d", mxconfig.getPixelColorDepthBits());
      display->setCursor(x, 22);
      display->printf("%d Hz", display->calculated_refresh_rate);
    }
  } else if (viewMode == 2) {
    display->fillScreen(display->color565(255, 255, 255));
  } else if (viewMode == 3) {
    display->fillScreen(display->color565(255, 0, 0));
  } else if (viewMode == 4) {
    display->fillScreen(display->color565(0, 255, 0));
  } else if (viewMode == 5) {
    display->fillScreen(display->color565(0, 0, 255));
  } else {
    drawName("Testgroup - testnameabcdefg");
    drawCountdown(123);
  }
  // Render once after processing input, without showing an intermediate frame.
  display->flipDMABuffer();
  viewDirty = false;
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
  // Keep receiving the latest game screen while a diagnostic view is selected.
  if (viewMode == 0) viewDirty = true;
}

void setup() {
  Serial.begin(115200);
  delay(1500);

  mxconfig.setPixelColorDepthBits(2); //빗뎁스
  mxconfig.double_buff = true;

  // 블리딩 생기면 이부분 바꿔보기
  mxconfig.i2sspeed = HUB75_I2S_CFG::HZ_10M;
  mxconfig.latch_blanking = 4;
  mxconfig.clkphase = false;
  
  display = new MatrixPanel_I2S_DMA(mxconfig);
  if (!display->begin()) {
    Serial.println("Display begin() FAILED");
    while (true) delay(1000);
  }

  display->setBrightness8(255);
  display->clearScreen();
  display->setTextWrap(false);
  serialLine.reserve(160);
  lastCommand.reserve(160);
  lastValidCommandAt = millis();
  pinMode(UP_BUTTON_PIN, INPUT_PULLUP);
  buttonRaw = buttonStable = digitalRead(UP_BUTTON_PIN) == LOW;
  buttonChangedAt = millis();
}

void loop() {
  bool pressed = digitalRead(UP_BUTTON_PIN) == LOW;
  uint32_t now = millis();
  if (pressed != buttonRaw) {
    buttonRaw = pressed;
    buttonChangedAt = now;
  }
  if (buttonRaw != buttonStable && now - buttonChangedAt >= 30) {
    buttonStable = buttonRaw;
    if (buttonStable) {
      viewMode = (viewMode + 1) % VIEW_MODE_COUNT;
      viewDirty = true;
    }
  }
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
    if (viewMode == 0) viewDirty = true;
  }
  if (viewDirty) drawView();
}
