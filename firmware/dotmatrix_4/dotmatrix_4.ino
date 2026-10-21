// ArduinoJson, ESP32-HUB75-MatrixPanel-I2S-DMA 라이브러리 사용
#include <ArduinoJson.h>
#include <ESP32-HUB75-MatrixPanel-I2S-DMA.h>
#include <Fonts/TomThumb.h> // Adafruit GFX에 포함된 작은 영문 폰트

// ===== 현장 조정값: 수정 후 다시 업로드 =====
const int TEXT_OFFSET_X = 0;       // 전체 글자 좌우 이동(px): - 왼쪽, + 오른쪽
const int TEXT_OFFSET_Y = 0;       // 전체 글자 상하 이동(px): - 위, + 아래
const uint8_t DISPLAY_BRIGHTNESS = 255; // 전체 화면 밝기: 0 꺼짐 ~ 255 최대
const bool USE_SMALL_FONT = false;  // true: 작은 폰트(4x6px 칸), false: 기존 폰트(6x8px 칸)
const int FONT_SIZE = 1;           // 선택한 폰트의 정수 배율: 1 최소, 2 두 배
const int PADDING_TOP = 12;        // 이름/카운트다운 기본 y좌표(px)
const int PADDING_LEFT = 16;       // 이름 왼쪽 여백(px)
const int PADDING_RIGHT = 16;      // 카운트다운 오른쪽 여백(px)
const uint32_t LINK_TIMEOUT_MS = 5000; // 명령 수신이 끊긴 뒤 OFFLINE 표시까지(ms)
// 이름/카운트다운/상태/마지막 테스트에 폰트 선택 적용. 진단 정보는 기존 폰트/배율 1.
// 작은 폰트는 영문/숫자/ASCII 기호 지원. 일정한 정렬을 위해 글자마다 4px 칸 사용.
// 오프셋은 글자 크기/간격을 바꾸지 않는다. 화면 밖 글자는 잘린다.

// ===== 패널 구동 설정: 패널 교체/문제 대응 시 조정 =====
#define PANEL_WIDTH 64
#define PANEL_HEIGHT 32
#define PANEL_CHAIN 3
#define UP_BUTTON_PIN 6
const uint8_t PIXEL_COLOR_DEPTH = 2; // 색상 비트 깊이
const auto PANEL_CLOCK = HUB75_I2S_CFG::HZ_10M; // 패널 전송 클럭, [8/10/15/16/20]
const uint8_t LATCH_BLANKING = 2;    // 래치 전후 소등 구간 2~4+
const bool CLOCK_PHASE = false;     // 클럭 위상

// ===== 내부 설정 및 상태 =====
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

const int CHAR_W = USE_SMALL_FONT ? 4 : 6;
const int STATUS_GLYPH_H = USE_SMALL_FONT ? 5 : 7; // 대문자 높이
const int DISPLAY_WIDTH = PANEL_WIDTH * PANEL_CHAIN;
const int COUNTDOWN_MAX_CHARS = 4; // 3자리 숫자 + 따옴표
const int NAME_REGION_END = DISPLAY_WIDTH - COUNTDOWN_MAX_CHARS * CHAR_W * FONT_SIZE - PADDING_RIGHT;
static_assert(FONT_SIZE >= 1, "FONT_SIZE must be at least 1");
static_assert(PADDING_LEFT >= 0 && PADDING_RIGHT >= 0, "Padding must be nonnegative");
static_assert(NAME_REGION_END >= PADDING_LEFT, "No space left for name");

// 모든 텍스트의 최종 좌표에 동일한 오프셋 적용
void setTextCursor(int x, int y) {
  display->setCursor(x + TEXT_OFFSET_X, y + TEXT_OFFSET_Y);
}

// 고정 폭으로 출력해 폰트를 바꿔도 이름 영역/숫자 우측 정렬 계산을 유지한다.
// y는 글자 상단 기준. TomThumb의 기준선은 상단보다 5px 아래이다.
void drawTextAt(const char *text, int x, int y) {
  const uint16_t color = display->color565(255, 255, 255);
  display->setFont(USE_SMALL_FONT ? &TomThumb : nullptr);
  display->setTextWrap(false);
  display->setTextSize(FONT_SIZE);
  display->setTextColor(color);
  if (USE_SMALL_FONT) {
    for (size_t i = 0; text[i] != '\0'; i++) {
      uint8_t c = (uint8_t)text[i];
      if (c < 32 || c > 126) c = '?';
      display->drawChar(x + TEXT_OFFSET_X, y + TEXT_OFFSET_Y + 5 * FONT_SIZE,
                        c, color, 0, FONT_SIZE);
      x += CHAR_W * FONT_SIZE;
    }
  } else {
    setTextCursor(x, y);
    display->print(text);
  }
  // 다음 진단 화면에 작은 폰트 설정이 남지 않도록 복원
  display->setFont(nullptr);
}

// 하이픈 앞뒤 공백을 하나로 통일
String formatName(const String &raw) {
  String out;
  out.reserve(raw.length() + 4);
  for (unsigned int i = 0; i < raw.length(); i++) {
    char c = raw[i];
    if (c == '-') {
      while (out.length() > 0 && out[out.length() - 1] == ' ') {
        out.remove(out.length() - 1);
      }
      out += " - ";
      while (i + 1 < raw.length() && raw[i + 1] == ' ') i++;
    } else {
      out += c;
    }
  }
  return out;
}

void drawName(const char *text) {
  // 지우는 영역의 경계도 이동해 이름과 카운트다운이 서로 지워지지 않게 한다.
  int boundary = constrain(NAME_REGION_END + TEXT_OFFSET_X, 0, DISPLAY_WIDTH);
  display->fillRect(0, 0, boundary, PANEL_HEIGHT, 0);
  int maxChars = (NAME_REGION_END - PADDING_LEFT) / (CHAR_W * FONT_SIZE);
  char buf[161]; // 작은 폰트에서도 수신 가능한 문자열을 충분히 보관
  maxChars = constrain(maxChars, 0, (int)sizeof(buf) - 1);
  strncpy(buf, text, maxChars);
  buf[maxChars] = '\0';
  drawTextAt(buf, PADDING_LEFT, PADDING_TOP);
}

void drawCountdown(int seconds) {
  int boundary = constrain(NAME_REGION_END + TEXT_OFFSET_X, 0, DISPLAY_WIDTH);
  display->fillRect(boundary, 0, DISPLAY_WIDTH - boundary, PANEL_HEIGHT, 0);
  char text[16];
  snprintf(text, sizeof(text), "%d\"", seconds);
  int x = DISPLAY_WIDTH - strlen(text) * CHAR_W * FONT_SIZE - PADDING_RIGHT;
  drawTextAt(text, x, PADDING_TOP);
}

void drawStatus(const char *raw) {
  String text(raw);
  text.toUpperCase();
  const int size = FONT_SIZE;
  text = text.substring(0, DISPLAY_WIDTH / (CHAR_W * size));
  int width = text.length() ? (text.length() * CHAR_W - 1) * size : 0;
  display->clearScreen();
  drawTextAt(text.c_str(), (DISPLAY_WIDTH - width) / 2,
             (PANEL_HEIGHT - STATUS_GLYPH_H * size) / 2);
}

void drawButtonPrompt(const char *raw) {
  String text(raw);
  text.toUpperCase();
  display->clearScreen();
  drawName(text.c_str());
  // Use the countdown region for a fixed arrow, independent of text length.
  const int width = min(18 * FONT_SIZE, COUNTDOWN_MAX_CHARS * CHAR_W * FONT_SIZE);
  const int right = DISPLAY_WIDTH - PADDING_RIGHT - 1 + TEXT_OFFSET_X;
  const int centerY = PADDING_TOP + STATUS_GLYPH_H * FONT_SIZE / 2 + TEXT_OFFSET_Y;
  const int head = 5 * FONT_SIZE;
  const uint16_t color = display->color565(255, 255, 255);
  // Thin, open arrow: one-pixel shaft and head, retaining the same bounds.
  display->drawFastHLine(right - width + 1, centerY, width, color);
  display->drawLine(right - head, centerY - 4 * FONT_SIZE, right, centerY, color);
  display->drawLine(right - head, centerY + 4 * FONT_SIZE, right, centerY, color);
}

String offlineText = "OFFLINE";

void drawRuntime() {
  display->clearScreen();
  if (linkTimedOut) {
    drawStatus(offlineText.c_str());
    return;
  }
  StaticJsonDocument<512> message;
  if (deserializeJson(message, lastCommand)) return;
  const char *type = message["type"] | "";
  if (!strcmp(type, "status")) {
    const char *arrow = message["arrow"] | "";
    if (!strcmp(arrow, "right")) drawButtonPrompt(message["text"].as<const char *>());
    else drawStatus(message["text"].as<const char *>());
  } else if (!strcmp(type, "display")) {
    String text = formatName(message["text"].as<const char *>());
    drawName(text.c_str());
    drawCountdown(message["seconds"].as<int>());
  }
}

void drawView() {
  display->clearScreen();
  display->setFont(nullptr);
  display->setTextWrap(false);
  display->setTextSize(1);
  display->setTextColor(display->color565(255, 255, 255));
  if (viewMode == 0) {
    drawRuntime();
  } else if (viewMode == 1) {
    for (int panel = 0; panel < PANEL_CHAIN; panel++) {
      int x = panel * PANEL_WIDTH + 2;
      setTextCursor(x, 2);
      display->printf("PANEL %d", panel + 1);
      setTextCursor(x, 12);
      display->printf("BIT %d", mxconfig.getPixelColorDepthBits());
      setTextCursor(x, 22);
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
  // 입력 처리 후 완성된 화면을 한 번에 표시
  display->flipDMABuffer();
  viewDirty = false;
}

void handleCommand(const String &line) {
  StaticJsonDocument<512> message;
  if (deserializeJson(message, line)) return;
  const char *type = message["type"] | "";
  if (strcmp(type, "clear") && strcmp(type, "status") && strcmp(type, "display")) return;
  if (strcmp(type, "clear") && !message["text"].is<const char *>()) return;
  if (!strcmp(type, "display") &&
      (!message["seconds"].is<int>() || message["seconds"].as<int>() < 0)) return;
  // 같은 명령도 연결 유지 신호로 처리
  if (message.containsKey("offline_text")) {
    if (!message["offline_text"].is<const char *>()) return;
    String candidate = message["offline_text"].as<const char *>();
    if (candidate.length() > 32) return;
    for (unsigned int i = 0; i < candidate.length(); i++) {
      if (candidate[i] < 32 || candidate[i] > 126) return;
    }
    offlineText = candidate;
  }
  lastValidCommandAt = millis();
  linkTimedOut = false;
  if (line == lastCommand) return;
  lastCommand = line;
  // 진단 화면에서도 최신 운영 명령은 계속 수신
  if (viewMode == 0) viewDirty = true;
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  mxconfig.setPixelColorDepthBits(PIXEL_COLOR_DEPTH);
  // 블리딩 발생 시 클럭/래치/위상 확인. 더블 버퍼는 화면 갱신 중 깜빡임 방지.
  mxconfig.double_buff = true; // 화면 버퍼 두 개 사용
  mxconfig.i2sspeed = PANEL_CLOCK;
  mxconfig.latch_blanking = LATCH_BLANKING;
  mxconfig.clkphase = CLOCK_PHASE;
  display = new MatrixPanel_I2S_DMA(mxconfig);
  if (!display->begin()) {
    Serial.println("Display begin() FAILED");
    while (true) delay(1000);
  }
  display->setBrightness8(DISPLAY_BRIGHTNESS);
  display->clearScreen();
  display->setTextWrap(false);
  serialLine.reserve(384);
  lastCommand.reserve(384);
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
  // 불완전하거나 너무 긴 수신 데이터를 정리
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
      if (serialLine.length() >= 384) {
        serialLine = "";
        droppingLine = true;
      } else {
        serialLine += value;
      }
    }
  }
  if (!linkTimedOut && millis() - lastValidCommandAt >= LINK_TIMEOUT_MS) {
    linkTimedOut = true;
    lastCommand = "";
    if (viewMode == 0) viewDirty = true;
  }
  if (viewDirty) drawView();
}
