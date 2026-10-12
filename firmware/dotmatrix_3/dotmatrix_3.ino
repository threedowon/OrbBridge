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
const uint32_t SERVER_TIMEOUT_MS = 5000;
uint32_t lastServerTick = 0;
bool serverWatchdogArmed = false;
String lastText;

// ===== 사용자 설정 변수 =====
const int PADDING_TOP    = 12;  // 상단 여백(px) — 텍스트 시작 y좌표
const int PADDING_BOTTOM = 0;   // 하단 여백(px)
const int PADDING_LEFT   = 16;   // 좌측 여백(px) — 이름 시작 x좌표
const int PADDING_RIGHT  = 16;   // 우측 여백(px) — 카운트다운 우측 끝 여백
const int FONT_SIZE      = 1;   // 폰트 크기 배율 (1 = 6x8px 기본 폰트)

// ============================

const int CHAR_W = 6;  // 기본 폰트 1글자 폭(배율 1 기준)
const int DISPLAY_WIDTH = PANEL_WIDTH * PANEL_CHAIN;
const int VIEW_MODE_COUNT = 7;  // 0:runtime 1:panel info 2~5:색상 6:test text
int lastSeconds = -1;
int viewMode = 0;
bool buttonPressed = false;
bool hasContent = false;
uint32_t lastButtonTick = 0;


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
  strncpy(buf, text, maxChars);
  buf[maxChars < 40 ? maxChars : 39] = '\0';
  display->print(buf);
}


void drawCountdown(int seconds) {
  display->fillRect(NAME_REGION_END, 0, DISPLAY_WIDTH - NAME_REGION_END, PANEL_HEIGHT, 0);
  display->setTextColor(display->color565(255, 255, 255));
  display->setTextSize(FONT_SIZE);

  char text[8];
  snprintf(text, sizeof(text), "%d\"", seconds);
  int x = DISPLAY_WIDTH - strlen(text) * CHAR_W * FONT_SIZE - PADDING_RIGHT;
  display->setCursor(x, PADDING_TOP);
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

// 순환 마지막의 테스트 문구 화면: Testgroup - testname ...... 123"
void drawTestText() {
  display->clearScreen();
  display->setTextWrap(false);
  drawName("Testgroup - testnameabcdefg");
  drawCountdown(123);
}

void drawRuntime() {
  display->clearScreen();
  if (!hasContent) return;
  drawName(lastText.c_str());
  drawCountdown(lastSeconds);
}

void changeView() {
  viewMode = (viewMode + 1) % VIEW_MODE_COUNT;
  if (viewMode == 1) {
    drawPanelInfo();
  } else if (viewMode == 2) {
    display->fillScreen(display->color565(255, 255, 255));
  } else if (viewMode == 3) {
    display->fillScreen(display->color565(255, 0, 0));
  } else if (viewMode == 4) {
    display->fillScreen(display->color565(0, 255, 0));
  } else if (viewMode == 5) {
    display->fillScreen(display->color565(0, 0, 255));
  } else if (viewMode == 6) {
    drawTestText();
  } else {
    drawRuntime();
  }
}

// A lost server must not leave the last frame (or a diagnostic view) lit.
void blankPanel() {
  display->clearScreen();
  hasContent = false;
  lastText = "";
  lastSeconds = -1;
  viewMode = 0;
}

void serverAlive() {
  lastServerTick = millis();
  serverWatchdogArmed = true;
}

void handleCommand(const String &line) {
  StaticJsonDocument<128> message;
  if (deserializeJson(message, line)) return;

  const char *type = message["type"] | "";
  if (!strcmp(type, "shutdown")) {
    blankPanel();
    serverWatchdogArmed = false;
    return;
  }
  if (!strcmp(type, "heartbeat")) {
    serverAlive();
    return;
  }
  if (!strcmp(type, "clear")) {
    serverAlive();
    hasContent = false;
    if (viewMode == 0) display->clearScreen();
    return;
  }
  if (strcmp(type, "display")) return;

  const char *text = message["text"] | "";
  int seconds = message["seconds"] | -1;
  if (seconds < 0) return;
  serverAlive();

  lastText = formatName(text);  // "그룹 - 멤버" 형식으로 정규화
  lastSeconds = seconds;
  hasContent = true;
  if (viewMode == 0) drawRuntime();
}

void setup() {
  Serial.begin(115200);
  delay(1500);

  mxconfig.setPixelColorDepthBits(2);
  mxconfig.i2sspeed = HUB75_I2S_CFG::HZ_20M;
  display = new MatrixPanel_I2S_DMA(mxconfig);
  if (!display->begin()) {
    Serial.println("Display begin() FAILED");
    while (true) delay(1000);
  }

  display->setBrightness8(255);
  display->clearScreen();
  serialLine.reserve(128);
  lastText.reserve(48);
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
  if (serverWatchdogArmed && uint32_t(millis() - lastServerTick) >= SERVER_TIMEOUT_MS) {
    blankPanel();
    serialLine = "";
    serverWatchdogArmed = false;
  }

}