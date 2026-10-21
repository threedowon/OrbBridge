/*
 * OrbBridge — Slave ESP32-S3  (이벤트 + Carrier-Sense + 바이너리 프레임)
 *
 * 16× OneWire → DS2431
 * RS485: slave 가 자율 전송(단방향). master 는 듣기만.
 *   - 전송은 13바이트 바이너리 프레임(+CRC8) — JSON 보다 ~5배 짧아 충돌·깨짐↓, CRC 로 검출.
 *   - 전송 전 버스가 조용한지 듣고(carrier-sense) 사용 중이면 랜덤 백오프.
 *   - 유실돼도 heartbeat 의 state 마스크로 PC 가 자가복구.
 *
 * 프레임(13B): [STX=0x02][TYPE][SLAVE][CELL][UID 0..7 (8B)][CRC8]
 *   TYPE 1=added 2=removed 3=heartbeat
 *   heartbeat 는 CELL=0, UID 자리 첫 2바이트에 state(비트마스크) 저장
 *   CRC8 = crc8(frame[1..11])
 *
 * RS485 핀·속도: TX=39, RX=40, DE=42(하드웨어 RTS), 115200 8N1
 * 보드 ID: DIP 스위치 3개(41/47/21)로 1~7. 6장 전부 같은 바이너리를 굽고
 *          DIP 만 다르게 맞춘다. 0 이면 미설정 — 부팅 시 경고.
 */

#include <OneWire.h>
#include <Adafruit_NeoPixel.h>
#include <HardwareSerial.h>
#include "LedCommandParser.h"
#include "HintCommandParser.h"
#include "ServerLease.h"

#define TX_PIN       39
#define RX_PIN       40
#define DE_PIN       42     // RS485 송수신 전환 — 하드웨어 RTS 가 직접 제어한다
// 이 보드(DevKitC-1, 실크 "RGB@IO38")의 온보드 RGB 는 GPIO 38 에 있고 48 에는
// 아무 LED 도 없다. 38 은 이미 LED 체인2 가 쓰고 PCB 가 그렇게 발주돼 바꿀 수 없다.
// 그래서 상태 표시(flashRed)는 48 로 나가지만 실제로 켜지는 LED 는 없다.
// 대신 온보드 RGB 가 체인2 의 첫 픽셀(슬롯 9)을 그대로 따라 한다 — 동작에는 무해.
// 48 에 외부 LED 를 달면 그때부터 상태 표시가 살아난다.
#define RGB_PIN      48
#define LED_BRIGHTNESS 32
#define RS485_BAUD   115200

// 보드 ID DIP 스위치 (INPUT_PULLUP — 스위치 ON 이면 GND 로 떨어져 LOW)
#define PIN_DIP_1    41     // +1
#define PIN_DIP_2    47     // +2
#define PIN_DIP_3    21     // +4
uint8_t slaveId = 0;        // setup 에서 DIP 로 결정한다

#define HEARTBEAT_MS 2000

// 접촉 불량으로 OneWire 읽기가 잠깐 실패해도 제거로 보지 않는다.
// 이 시간 동안 계속 못 읽어야 실제 제거로 인정한다. loop 주기가 LED
// 이펙트·RS485 트래픽에 따라 흔들리므로 횟수가 아니라 시간으로 센다.
// 접촉이 계속 튀면 이 값을 키운다(제거 반응이 그만큼 늦어진다).
#define REMOVE_DEBOUNCE_MS 300

// 링 밝기 페이드 갱신 주기. 이 간격으로만 다시 그려서 strip.show() 부담을 줄인다.
#define FADE_STEP_MS 30

// LED 데이터선 2개. 슬롯별 LED 위치는 아래 LED_MAP 으로 배선에 맞게 직접 지정한다.
#define RING_PIN_LOW    2      // LED 체인 1 = 슬롯 1~8  (GPIO 2)
#define RING_PIN_HIGH   38     // LED 체인 2 = 슬롯 9~16 (GPIO 38 — 온보드 RGB 와 공유)
#define LEDS_PER_RING   10     // 슬롯당 LED 수
#define LED_COUNT_LOW   80     // 스트립 A 총 LED 수 (슬롯 1~8 = 8×10)
#define LED_COUNT_HIGH  80     // 스트립 B 총 LED 수 (슬롯 9~16 = 8×10)
// 지금 40 LED(슬롯5~8)만 연결이라 50 으로 올림(~0.4A). LED 많이 늘리면 외부 5V + 밝기 조정.
#define RING_BRIGHTNESS 50

#define DEBUG_USB    0

// Carrier-sense (115200 에서는 한 프레임이 ~1.1ms 라 4ms 는 과하게 길다)
#define BUS_IDLE_MS  1
#define CS_MAX_TRIES 20

// added·removed 는 유실 대비 같은 프레임을 여러 번 보낸다. 반복 사이에 랜덤
// 간격을 둬 다른 보드와 겹치는 것을 피한다. 서버가 중복을 걸러낸다.
#define FRAME_REPEAT       3
#define REPEAT_GAP_MIN_MS  5
#define REPEAT_GAP_RAND_MS 10

// 바이너리 프레임
#define FRAME_LEN 13
#define STX       0x02
#define T_ADDED   1
#define T_REMOVED 2
#define T_HEART   3
#define T_LED_ACK 5
#define T_LED_ACK_TIMING 6

Adafruit_NeoPixel led(1, RGB_PIN, NEO_GRB + NEO_KHZ800);
HardwareSerial RS485(1);

const int BUS_COUNT = 16;
OneWire buses[BUS_COUNT] = {
  OneWire(4), OneWire(5), OneWire(6), OneWire(7),
  OneWire(15), OneWire(16), OneWire(17), OneWire(18),
  // PCB SLOT09..SLOT15 = GPIO8..GPIO14; SLOT16 = GPIO1 (J6 pin 4).
  OneWire(8), OneWire(9), OneWire(10), OneWire(11),
  OneWire(12), OneWire(13), OneWire(14), OneWire(1)
};

Adafruit_NeoPixel ringLow (LED_COUNT_LOW,  RING_PIN_LOW,  NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel ringHigh(LED_COUNT_HIGH, RING_PIN_HIGH, NEO_GRB + NEO_KHZ800);

// ── 슬롯(버스) → LED 위치 표 ──  배선이 바뀌면 여기만 고친다.
//   strip: 0=스트립A(RING_PIN_LOW), 1=스트립B(RING_PIN_HIGH), -1=LED 없음
//   offset: 그 스트립에서 이 슬롯의 첫 LED 인덱스 (슬롯당 LEDS_PER_RING 개)
// 인덱스 = 버스 index (0=슬롯1 … 15=슬롯16)
struct LedMap { int8_t strip; int16_t offset; };
const LedMap LED_MAP[BUS_COUNT] = {
  { 0,  0}, { 0, 10}, { 0, 20}, { 0, 30},   // 슬롯 1~4  : 체인1(GPIO2)  0/10/20/30
  { 0, 40}, { 0, 50}, { 0, 60}, { 0, 70},   // 슬롯 5~8  : 체인1(GPIO2)  40/50/60/70
  { 1,  0}, { 1, 10}, { 1, 20}, { 1, 30},   // 슬롯 9~12 : 체인2(GPIO38) 0/10/20/30
  { 1, 40}, { 1, 50}, { 1, 60}, { 1, 70},   // 슬롯 13~16: 체인2(GPIO38) 40/50/60/70
};

byte lastAddr[BUS_COUNT][8];
bool hasLast[BUS_COUNT] = {false};
uint8_t ringLevel[BUS_COUNT] = {0};      // 슬롯 링 밝기 0-255
unsigned long lastFadeMs[BUS_COUNT] = {0};    // 마지막 페이드 갱신 시각
bool missing[BUS_COUNT] = {false};        // 미검출 구간에 들어갔는지
unsigned long missSinceMs[BUS_COUNT] = {0};   // 그 구간이 시작된 시각
unsigned long lastHeartbeatMs = 0;
unsigned long lastBusRxMs = 0;
unsigned long ledOffAt = 0;

// LED 테스트 이펙트 (master 가 debug 도구에서 "C<n>" 명령을 RS485 로 브로드캐스트).
//   0=일반(오브 감지)  1=흰색전체  2=끄기  3=빨강 4=초록 5=파랑
//   6=무지개(정지)  7=체이스(움직임)  8=숨쉬기  9=무지개(회전)
int ledEffect = 0;
unsigned long effectStart = 0;
unsigned long lastAnimMs = 0;
LedCommandParser ledCommandParser;
HintCommandParser hintParser;
HintCommandParser usbHintParser;
bool hintActive = false;
bool playbackCursor = false;
bool dfestaCursor = false;
uint8_t dfestaMasks[2] = {0, 0};
bool dfestaReleasing = false;
bool dfestaBlocked = false;
uint32_t dfestaReleaseStarted = 0;
uint8_t dfestaReleaseLevels[BUS_COUNT] = {};
uint16_t breathMask = 0;
uint32_t breathStarted = 0, breathRenewed = 0, breathFrame = 0;
bool hintNoteLit = false;
uint32_t hintStarted = 0;
uint16_t hintDuration = 0;
uint16_t hintFadeMs = 600;
bool trayGlow = false;
uint16_t trayRiseMs = 0;
uint8_t trayEasing = 0;
bool hintEnding = false;
uint32_t hintEndStarted = 0;
bool hintRingLit[BUS_COUNT] = {};
uint32_t hintRingStarted[BUS_COUNT] = {};
uint16_t hintRingDuration[BUS_COUNT] = {};
uint8_t hintRingBrightness[BUS_COUNT] = {};
uint32_t hintFrameAt = 0;
String usbCmdLine;
bool ledAckPending = false;
uint8_t ledAckEffect = 0;
uint32_t ledReceivedMs = 0;
uint32_t ledAppliedMs = 0;
bool usbDirect = false;
ServerLease serverLease;
bool slotLedsOnline = false;  // Boot dark, even if eggs are already on the table.
// USB diagnostics: distinguish an idle scanner, no ROM, and ROM CRC failures.
uint32_t usbScanCount = 0;
uint32_t usbLedCommandCount = 0;
uint16_t usbRomSeenMask = 0;
uint16_t usbCrcErrorMask = 0;
void sendHeartbeat();
void applyEffectOnce();   // 전방 선언
void runEffectAnim();
void fillAll(uint32_t color);

void updateServerLease() {
  const bool online = serverLease.online(millis());
  if (!online) usbDirect = false;
  if (online == slotLedsOnline) return;
  slotLedsOnline = online;
  // Discard all old animations on either transition; keep scanning/occupancy.
  dfestaReleasing = false;
  dfestaBlocked = true;
  dfestaCursor = false;
  dfestaMasks[0] = dfestaMasks[1] = 0;
  breathMask = 0;
  hintActive = playbackCursor = hintNoteLit = hintEnding = trayGlow = false;
  memset(hintRingLit, 0, sizeof(hintRingLit));
  ledAckPending = false;
  ledEffect = 0;
  if (online) applyEffectOnce();
  else fillAll(0);
}

void applyLedCommand(int effect) {
  if (!slotLedsOnline) return;
  dfestaReleasing = false;
  dfestaBlocked = true;
  dfestaCursor = false;
  dfestaMasks[0] = dfestaMasks[1] = 0;
  breathMask = 0;
  hintActive = false;
  ledReceivedMs = millis();
  ledEffect = effect;
  effectStart = ledReceivedMs;
  applyEffectOnce();
  if (effect >= 7) {
    lastAnimMs = millis() - 30;
    runEffectAnim();  // Acknowledge only after the first frame is output.
  }
  ledAppliedMs = millis();
  ledAckEffect = effect;
  ledAckPending = true;
}

// Debug PC renews U1 every second. Expiry blacks out LEDs and restores RS485.
// USB commands never propagate to the RS485 bus.
void pollUsb() {
  updateServerLease();
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n') {
      if (usbCmdLine == "U1") {
        bool entering = !usbDirect;
        if (entering) { serverLease.disable(); updateServerLease(); }
        usbDirect = true;
        serverLease.renew(millis());
        updateServerLease();
        if (entering) sendHeartbeat();
      } else if (usbCmdLine == "U0" && usbDirect) {
        serverLease.disable();
        updateServerLease();
      } else if (usbDirect && usbHintParser.parse(usbCmdLine.c_str()) && usbHintParser.lease < 0) {
        applyHintCommand(usbHintParser);
      } else if (usbDirect && usbCmdLine.length() == 2 &&
                 usbCmdLine[0] == 'C' && usbCmdLine[1] >= '0' && usbCmdLine[1] <= '9') {
        usbLedCommandCount++;
        applyLedCommand(usbCmdLine[1] - '0');
      }
      usbCmdLine = "";
    } else if (c != '\r') {
      if (usbCmdLine.length() < 24) usbCmdLine += c;
      else usbCmdLine = "!";
    }
  }
}

void emitUsb(uint8_t type, uint8_t cell, const byte* uid, uint16_t state) {
  String json = String("{\"type\":\"") +
    (type == T_HEART ? "heartbeat" : type == 4 ? "snapshot" : type == T_ADDED ? "added" : "removed") +
    "\",\"transport\":\"usb\",\"slave\":" + slaveId;
  if (type == T_HEART) {
    json += String(",\"state\":") + state;
    json += ",\"firmware\":\"usb-diag-1\"";
    json += String(",\"scan_count\":") + usbScanCount;
    json += String(",\"rom_seen_mask\":") + usbRomSeenMask;
    json += String(",\"crc_error_mask\":") + usbCrcErrorMask;
    json += String(",\"led_effect\":") + ledEffect;
    json += String(",\"led_commands\":") + usbLedCommandCount;
    usbRomSeenMask = 0;
    usbCrcErrorMask = 0;
  } else {
    const char* hx = "0123456789ABCDEF";
    json += String(",\"cell\":") + cell + ",\"id\":\"";
    for (int i = 0; i < 8; i++) {
      if (i) json += ':';
      json += hx[uid[i] >> 4];
      json += hx[uid[i] & 15];
    }
    json += '"';
  }
  Serial.println(json + "}");
}

// Dallas/Maxim CRC8 (OneWire 와 동일 다항식)
uint8_t crc8(const uint8_t* data, int len) {
  uint8_t crc = 0;
  for (int i = 0; i < len; i++) {
    uint8_t inbyte = data[i];
    for (int j = 0; j < 8; j++) {
      uint8_t mix = (crc ^ inbyte) & 0x01;
      crc >>= 1;
      if (mix) crc ^= 0x8C;
      inbyte >>= 1;
    }
  }
  return crc;
}

// RS485 수신 처리: carrier-sense 활동 감지 + master LED 명령("C<n>\n") 파싱.
// 바이너리 프레임의 UID/CRC 안에도 C, 숫자, 개행이 들어갈 수 있으므로 분리한다.
void drainBus() {
  updateServerLease();
  while (RS485.available()) {
    unsigned char c = (unsigned char)RS485.read();
    lastBusRxMs = millis();
    int effect = ledCommandParser.feed(c, lastBusRxMs);
    bool hintReady = hintParser.feed(c, lastBusRxMs);
    if (!usbDirect && hintReady) {
      if (hintParser.lease >= 0) {
        if (hintParser.lease) serverLease.renew(millis());
        else serverLease.disable();
        updateServerLease();
      } else applyHintCommand(hintParser);
    }
    if (!usbDirect && effect >= 0) {
      applyLedCommand(effect);
    }
  }
}

// carrier-sense 로 바이너리 프레임 전송 (조용할 때 + 랜덤 백오프)
void csSendBytes(const uint8_t* buf, int len, uint32_t ackAppliedMs) {
  for (int t = 0; t < CS_MAX_TRIES; t++) {
    drainBus();
    if (millis() - lastBusRxMs >= BUS_IDLE_MS) {
      // 백오프는 순수 랜덤(ID 편향 X → 공정). de-sync 는 보드별 randomSeed 로 이미 확보.
      unsigned long backoff = 1 + (unsigned long)random(0, 12);
      unsigned long t0 = millis();
      while (millis() - t0 < backoff) drainBus();
      if (millis() - lastBusRxMs >= BUS_IDLE_MS) {
        if (len == FRAME_LEN && buf[1] == T_LED_ACK_TIMING) {
          uint8_t timed[FRAME_LEN];
          memcpy(timed, buf, FRAME_LEN);
          uint32_t waitMs = millis() - ackAppliedMs;
          uint16_t bounded = waitMs > 65535UL ? 65535 : (uint16_t)waitMs;
          timed[10] = bounded & 0xff;
          timed[11] = bounded >> 8;
          timed[12] = crc8(&timed[1], 11);
          RS485.write(timed, FRAME_LEN);
        } else {
          RS485.write(buf, len);
        }
        RS485.flush();
        lastBusRxMs = millis();
        return;
      }
    } else {
      delay(1);
    }
  }
  // 계속 바쁨 → 이번 전송 포기(자가복구가 메꿈)
}

void sendFrame(uint8_t type, uint8_t cell, const byte* uid, uint16_t state) {
  if (usbDirect) {
    emitUsb(type, cell, uid, state);
    return;
  }
  uint8_t f[FRAME_LEN];
  f[0] = STX;
  f[1] = type;
  f[2] = slaveId;
  f[3] = cell;
  if (type == T_HEART) {
    f[4] = (uint8_t)(state & 0xFF);
    f[5] = (uint8_t)((state >> 8) & 0xFF);
    for (int i = 6; i < 12; i++) f[i] = 0;
  } else {
    for (int i = 0; i < 8; i++) f[4 + i] = uid[i];
  }
  f[12] = crc8(&f[1], 11);
  csSendBytes(f, FRAME_LEN, 0);
}

// 반복 전송 사이의 간격. 그냥 delay 하면 그동안 master 의 LED 명령을 놓치고
// carrier-sense 기준 시각도 낡으므로, 기다리는 동안에도 버스를 계속 읽는다.
void pumpFor(unsigned long ms) {
  unsigned long t0 = millis();
  while (millis() - t0 < ms) drainBus();
}

// 유실 대비 같은 프레임을 FRAME_REPEAT 번 보낸다. 서버가 중복을 걸러내므로
// 소리나 상태가 여러 번 반영되지는 않는다.
void sendFrameRepeated(uint8_t type, uint8_t cell, const byte* uid) {
  if (usbDirect) {
    emitUsb(type, cell, uid, 0);
    return;
  }
  for (int r = 0; r < FRAME_REPEAT; r++) {
    sendFrame(type, cell, uid, 0);
    if (r + 1 < FRAME_REPEAT) {
      pumpFor(REPEAT_GAP_MIN_MS + (unsigned long)random(0, REPEAT_GAP_RAND_MS));
    }
  }
}

void flashRed() {
  led.setPixelColor(0, led.Color(255, 0, 0));
  led.show();
  ledOffAt = millis() + 80;
}

void updateLed() {
  if (ledOffAt != 0 && millis() >= ledOffAt) {
    led.clear();
    led.show();
    ledOffAt = 0;
  }
}

void setRing(int busIndex, uint32_t color, bool showNow = true) {
  if (!slotLedsOnline) color = 0;
  const LedMap& m = LED_MAP[busIndex];
  if (m.strip < 0) return;                        // 이 슬롯은 LED 없음 → 무시
  Adafruit_NeoPixel& strip = (m.strip == 0) ? ringLow : ringHigh;
  for (int j = 0; j < LEDS_PER_RING; j++) strip.setPixelColor(m.offset + j, color);
  if (showNow) strip.show();
}

// Use the same gold RGB ratio for playback and occupancy.
uint32_t orbGold(uint8_t brightness) {
  return Adafruit_NeoPixel::Color(
    brightness,
    (uint16_t(147) * brightness + 127) / 255,
    (uint16_t(41) * brightness + 127) / 255);
}

// 슬롯 링을 지정한 밝기로 그린다. 이펙트 모드에서는 링을 건드리지 않지만
// 밝기는 계속 추적해, 이펙트를 끄면 실제 배치와 다시 맞는다.
// The accumulated column follows its answer orb's normal contact fade.
int dfestaAnswer(int column) {
  for (int pitch = 7; pitch >= 0; --pitch)
    if (dfestaMasks[column] & (1 << pitch)) return column * 8 + pitch;
  return -1;
}

void drawDfestaColumn(int column) {
  const int answer = dfestaAnswer(column);
  const uint8_t level = answer >= 0 ? ringLevel[answer] : 0;
  for (int pitch = 0; pitch < 8; ++pitch)
    setRing(column * 8 + pitch,
      (dfestaMasks[column] & (1 << pitch)) ? orbGold(level) : 0, false);
}

void setSlotLevel(int i, uint8_t level) {
  // During ready breathing, a missing contact must not compete with the pulse.
  if (!hintActive && (breathMask & (uint16_t(1) << i)) && missing[i]) level = 0;
  ringLevel[i] = level;
  if (!slotLedsOnline) return;
  if (hintActive && dfestaCursor && !dfestaReleasing && dfestaAnswer(i / 8) == i) {
    drawDfestaColumn(i / 8);
    ringLow.show(); ringHigh.show();
  }
  if (ledEffect != 0 || hintActive) return;
  setRing(i, orbGold(level));
}

// ── LED 테스트 이펙트 ───────────────────────────────────
void fillAll(uint32_t color) {
  if (!slotLedsOnline) color = 0;
  for (uint16_t i = 0; i < LED_COUNT_LOW; i++)  ringLow.setPixelColor(i, color);
  for (uint16_t i = 0; i < LED_COUNT_HIGH; i++) ringHigh.setPixelColor(i, color);
  ringLow.show();
  ringHigh.show();
}

void stopHint() {
  dfestaReleasing = false;
  dfestaMasks[0] = dfestaMasks[1] = 0;
  dfestaCursor = false;
  if (!hintActive) return;
  hintActive = false;
  playbackCursor = false;
  hintEnding = false;
  trayGlow = false;
  memset(hintRingLit, 0, sizeof(hintRingLit));
  if (ledEffect != 0) fillAll(0);
  applyEffectOnce();
}

void releaseDfesta() {
  // Late beats from the cancelled visual run cannot rebuild the columns.
  // A new DFESTA run always sends H0,0,1 before its first Q command.
  dfestaBlocked = true;
  if (dfestaReleasing) return;
  if (!hintActive || !dfestaCursor) { stopHint(); return; }
  for (int i = 0; i < BUS_COUNT; ++i) {
    const int answer = dfestaAnswer(i / 8);
    dfestaReleaseLevels[i] = answer >= 0 && (dfestaMasks[i / 8] & (1 << (i % 8)))
      ? ringLevel[answer] : 0;
  }
  breathMask = 0;
  dfestaReleaseStarted = millis();
  dfestaReleasing = true;
}

void applyHintCommand(const HintCommandParser& command) {
  if (!slotLedsOnline) return;
  if (command.breathing) {
    uint16_t mask = 0;
    if (slaveId >= 1 && slaveId <= 6) {
      for (int column = 0; column < 2; ++column) {
        int pitch = command.breathColumns[(slaveId - 1) * 2 + column] - '0';
        if (pitch > 0 && hasLast[column * 8 + pitch - 1])
          mask |= uint16_t(1) << (column * 8 + pitch - 1);
      }
    }
    const bool changed = mask != breathMask;
    if (mask && !breathMask) breathStarted = millis();
    breathMask = mask; breathRenewed = millis();
    if (changed && !hintActive) applyEffectOnce();
    return;
  }
  if (command.tray) {
    dfestaReleasing = false;
    dfestaBlocked = true;
    dfestaCursor = false;
    dfestaMasks[0] = dfestaMasks[1] = 0;
    trayGlow = true;
    hintActive = true;
    playbackCursor = false;
    hintEnding = false;
    hintStarted = millis();
    hintDuration = command.duration;
    trayEasing = command.trayEasing;
    // Keep the original total duration; shorten both phases for short delays.
    trayRiseMs = command.duration / 2 < 800 ? command.duration / 2 : 800;
    hintFadeMs = command.duration - trayRiseMs < 800 ? command.duration - trayRiseMs : 800;
    hintFrameAt = hintStarted;
    memset(hintRingLit, 0, sizeof(hintRingLit));
    fillAll(0);  // All rows fade in together from black.
    return;
  }
  trayGlow = false;
  if (!command.playback) hintFadeMs = command.fadeMs;
  if (!command.duration) { stopHint(); return; }
  // H4: clear accumulated result and hold blackout until a new LED session.
  if (!command.playback && !command.slave && command.duration == 4) {
    dfestaReleasing = false;
    dfestaBlocked = true;
    dfestaMasks[0] = dfestaMasks[1] = 0;
    breathMask = 0;
    hintActive = playbackCursor = dfestaCursor = true;
    fillAll(0);
    return;
  }
  // H5: fade the result away and resume ordinary orb sensing.
  if (!command.playback && !command.slave && command.duration == 5) {
    releaseDfesta();
    return;
  }
  // Control values: 1=new session, 2=skip expired cue, 3=finish after tails.
  if (!command.playback && !command.slave && command.duration == 2) return;
  if (!command.playback && !command.slave && command.duration == 3) {
    if (hintActive && !playbackCursor) { hintEnding = true; hintEndStarted = millis(); }
    return;
  }
  if (command.dfesta) {
    if (dfestaBlocked) return;
    if (!dfestaCursor) dfestaMasks[0] = dfestaMasks[1] = 0;
    hintActive = playbackCursor = dfestaCursor = true;
    hintEnding = false;
    if (command.slave == slaveId) {
      const int column = command.cell - 1;
      int answer = -1;
      for (int pitch = 0; pitch < 8; ++pitch)
        if (command.mask & (1 << pitch)) answer = pitch;
      // Do not relight a column whose answer egg was already removed.
      dfestaMasks[column] = answer >= 0 && hasLast[column * 8 + answer] ? command.mask : 0;
    }
    for (int column = 0; column < 2; ++column) drawDfestaColumn(column);
    ringLow.show(); ringHigh.show();
    return;
  }
  dfestaReleasing = false;
  if (!command.playback && !command.slave && command.duration == 1)
    dfestaBlocked = false;
  dfestaMasks[0] = dfestaMasks[1] = 0;
  const bool resetRings = !hintActive || playbackCursor || command.playback || !command.slave;
  if (resetRings) {
    memset(hintRingLit, 0, sizeof(hintRingLit));
    if (!command.playback) fillAll(0);
  }
  hintEnding = false;
  hintActive = true;
  hintStarted = millis();
  hintDuration = command.duration;
  playbackCursor = command.playback;
  dfestaCursor = command.dfesta;
  hintNoteLit = command.slave == slaveId;
  if (playbackCursor) {
    // Restore the previous column to its tracked occupancy, then overlay this beat.
    for (int i = 0; i < BUS_COUNT; ++i) {
      // Use confirmed occupancy, not the temporary fade caused by a missed ROM read.
      uint8_t level = (!dfestaCursor && hasLast[i]) ? 255 : 0;
      setRing(i, Adafruit_NeoPixel::Color(level, uint16_t(147) * level / 255,
                                        uint16_t(41) * level / 255), false);
    }
    if (command.slave == slaveId) {
      for (int pitch = 0; pitch < 8; ++pitch) {
        const bool sounding = command.mask & (1 << pitch);
        setRing((command.cell - 1) * 8 + pitch, (!dfestaCursor || sounding)
          ? orbGold(255)
          : 0, false);
      }
    }
    ringLow.show();
    ringHigh.show();
    return;
  }
  if (command.slave == slaveId) {
    const int slot = command.cell - 1;
    hintRingLit[slot] = true;
    hintRingStarted[slot] = hintStarted;
    hintRingDuration[slot] = command.duration;
    hintRingBrightness[slot] = 255;
    setRing(command.cell - 1, Adafruit_NeoPixel::Color(255, 147, 41));  // 오브 인식과 같은 따뜻한 색
  }
}

void updateHint() {
  if (!slotLedsOnline || !hintActive) return;
  if (dfestaReleasing) {
    const uint32_t age = uint32_t(millis() - dfestaReleaseStarted);
    if (age >= REMOVE_DEBOUNCE_MS) { stopHint(); return; }
    if (uint32_t(millis() - hintFrameAt) < FADE_STEP_MS) return;
    hintFrameAt = millis();
    for (int i = 0; i < BUS_COUNT; ++i) {
      uint8_t level = uint32_t(dfestaReleaseLevels[i]) * (REMOVE_DEBOUNCE_MS - age) / REMOVE_DEBOUNCE_MS;
      // Keep actual orbs visible, including new placements during the fade.
      if (ringLevel[i] > level) level = ringLevel[i];
      setRing(i, orbGold(level), false);
    }
    ringLow.show(); ringHigh.show();
    return;
  }
  if (dfestaCursor) return;  // Result remains until egg removal or explicit reset.
  const uint32_t elapsed = uint32_t(millis() - hintStarted);
  if (trayGlow) {
    if (uint32_t(millis() - hintFrameAt) < 20) return;
    hintFrameAt = millis();
    float progress = !trayRiseMs || elapsed >= trayRiseMs ? 1.0f : float(elapsed) / trayRiseMs;
    if (trayEasing == 1) progress *= progress;
    else if (trayEasing == 2) progress = 1.0f - (1.0f - progress) * (1.0f - progress);
    const uint32_t fadeStart = hintDuration - hintFadeMs;
    uint8_t level = uint8_t(progress * 255);
    if (elapsed >= fadeStart)
      level = elapsed >= hintDuration ? 0 : uint32_t(255) * (hintDuration - elapsed) / hintFadeMs;
    // Avoid the red tail caused by global-brightness channel quantization.
    if (level < 40) level = 0;
    fillAll(orbGold(level));
    // Hold blackout until the first hint starts, just as before.
    if (elapsed >= hintDuration) trayGlow = false;
    return;
  }

  if (playbackCursor && elapsed >= hintDuration) {
    if (dfestaCursor) {
      if (hintNoteLit) { fillAll(0); hintNoteLit = false; }
      if (elapsed >= 120000UL) stopHint();
    } else {
      // Keep the PLAY override between beats. Brief sensor misses must not
      // restore the normal contact-fade animation during playback.
      if (hintNoteLit) {
        for (int i = 0; i < BUS_COUNT; ++i)
          setRing(i, hasLast[i] ? orbGold(255) : 0, false);
        ringLow.show(); ringHigh.show();
        hintNoteLit = false;
      }
      if (elapsed >= 2500UL + hintDuration) stopHint();
    }
    return;
  }
  if (!playbackCursor && uint32_t(millis() - hintFrameAt) >= 20) {
    hintFrameAt = millis();
    bool anyLit = false;
    for (int i = 0; i < BUS_COUNT; ++i) {
      if (!hintRingLit[i]) continue;
      const uint32_t age = uint32_t(hintFrameAt - hintRingStarted[i]);
      uint8_t level = 255;
      if (age >= hintRingDuration[i]) {
        const uint32_t fadeAge = age - hintRingDuration[i];
        level = fadeAge >= hintFadeMs ? 0 : uint32_t(255) * (hintFadeMs - fadeAge) / hintFadeMs;
      }
      // setBrightness(50) quantizes the channels a second time. Stop before
      // the blue component vanishes and only a red/orange tail remains.
      if (trayGlow && level < 40) level = 0;
      if (level != hintRingBrightness[i]) {
        setRing(i, trayGlow ? orbGold(level) : Adafruit_NeoPixel::Color(level, uint16_t(147) * level / 255, uint16_t(41) * level / 255));
        hintRingBrightness[i] = level;
      }
      hintRingLit[i] = level > 0;
      anyLit |= hintRingLit[i];
    }
    if (hintEnding && !anyLit && uint32_t(millis() - hintEndStarted) >= hintFadeMs) { stopHint(); return; }
  }
  // Recover if the server disconnects and the explicit end command is lost.
  if (elapsed >= 120000UL) stopHint();
}

void rainbowStatic() {
  if (!slotLedsOnline) return;
  for (uint16_t i = 0; i < LED_COUNT_LOW; i++)
    ringLow.setPixelColor(i, Adafruit_NeoPixel::ColorHSV((uint16_t)(i * 65536L / LED_COUNT_LOW)));
  for (uint16_t i = 0; i < LED_COUNT_HIGH; i++)
    ringHigh.setPixelColor(i, Adafruit_NeoPixel::ColorHSV((uint16_t)(i * 65536L / LED_COUNT_HIGH)));
  ringLow.show();
  ringHigh.show();
}

// 명령 받은 즉시 적용되는 정적 이펙트. 애니메이션(7~9)은 loop 의 runEffectAnim 에서 갱신.
void applyEffectOnce() {
  if (!slotLedsOnline) return;
  switch (ledEffect) {
    case 0:
      // Restore tracked occupancy immediately. scanBus does not redraw a ring
      // whose level is already 255, so clearing alone leaves occupied rings dark.
      for (int i = 0; i < BUS_COUNT; i++) setSlotLevel(i, ringLevel[i]);
      break;
    case 1: fillAll(Adafruit_NeoPixel::Color(255, 255, 255)); break;  // 전체 흰색
    case 2: fillAll(0); break;                                        // 전체 끄기
    case 3: fillAll(Adafruit_NeoPixel::Color(255, 0, 0)); break;      // 빨강
    case 4: fillAll(Adafruit_NeoPixel::Color(0, 255, 0)); break;      // 초록
    case 5: fillAll(Adafruit_NeoPixel::Color(0, 0, 255)); break;      // 파랑
    case 6: rainbowStatic(); break;                                   // 무지개(정지)
    default: break;                                                   // 7~9: 애니메이션
  }
}

// 애니메이션 이펙트 (loop 에서 ~30ms 마다 호출)
void runEffectAnim() {
  if (!slotLedsOnline || hintActive) return;
  if (breathMask && uint32_t(millis() - breathRenewed) > 2500) {
    breathMask = 0; applyEffectOnce();
  }
  if (breathMask) {
    if (uint32_t(millis() - breathFrame) < 30) return;
    breathFrame = millis();
    const float phase = float(uint32_t(breathFrame - breathStarted) % 2000) / 2000.0f;
    // Enter at the normal occupied level (255), then dim smoothly. Starting
    // at the trough makes every reactivation look like a brief blackout.
    const uint8_t level = 32 + uint8_t(223 * (0.5f + 0.5f * cosf(phase * 6.2831853f)));
    for (int i = 0; i < BUS_COUNT; ++i)
      if (breathMask & (uint16_t(1) << i))
        setRing(i, hasLast[i] && !missing[i] ? orbGold(level) : 0);
    return;
  }
  if (ledEffect < 7) return;
  if (millis() - lastAnimMs < 30) return;
  lastAnimMs = millis();
  unsigned long t = millis() - effectStart;

  if (ledEffect == 7) {                 // 체이스 (흐르는 빛)
    int total = LED_COUNT_LOW;
    int pos = (t / 60) % total;
    uint32_t on = Adafruit_NeoPixel::Color(0, 180, 255);
    for (uint16_t i = 0; i < LED_COUNT_LOW; i++)
      ringLow.setPixelColor(i, ((int)i == pos || (int)i == (pos + 1) % total) ? on : 0);
    for (uint16_t i = 0; i < LED_COUNT_HIGH; i++)
      ringHigh.setPixelColor(i, ((int)i == pos || (int)i == (pos + 1) % total) ? on : 0);
    ringLow.show(); ringHigh.show();
  } else if (ledEffect == 8) {          // 숨쉬기 (밝기 fade)
    float ph = (t % 2000) / 2000.0f;
    int b = (int)((0.5f - 0.5f * cos(ph * 2 * PI)) * 255);
    fillAll(Adafruit_NeoPixel::Color((b * 60) / 255, (b * 120) / 255, (uint8_t)b));
  } else if (ledEffect == 9) {          // 무지개 회전
    uint16_t base = (uint16_t)(t * 8);
    for (uint16_t i = 0; i < LED_COUNT_LOW; i++)
      ringLow.setPixelColor(i, Adafruit_NeoPixel::ColorHSV(base + (uint16_t)(i * 65536L / LED_COUNT_LOW)));
    for (uint16_t i = 0; i < LED_COUNT_HIGH; i++)
      ringHigh.setPixelColor(i, Adafruit_NeoPixel::ColorHSV(base + (uint16_t)(i * 65536L / LED_COUNT_HIGH)));
    ringLow.show(); ringHigh.show();
  }
}

bool sameAddr(const byte a[8], const byte b[8]) {
  for (int i = 0; i < 8; i++) if (a[i] != b[i]) return false;
  return true;
}

void sendHeartbeat() {
  uint16_t state = 0;
  for (int i = 0; i < BUS_COUNT; i++) if (hasLast[i]) state |= (uint16_t)(1u << i);
  sendFrame(T_HEART, 0, nullptr, state);
  // Refresh occupied UIDs too, including orbs present before USB connection.
  if (usbDirect) {
    for (int i = 0; i < BUS_COUNT; i++)
      if (hasLast[i]) emitUsb(4, i + 1, lastAddr[i], 0);
  }
}

bool readBus(int i, byte addr[8]) {
  usbScanCount++;
  buses[i].reset_search();
  if (!buses[i].search(addr)) return false;
  usbRomSeenMask |= (uint16_t)(1u << i);
  if (OneWire::crc8(addr, 7) != addr[7]) {
    usbCrcErrorMask |= (uint16_t)(1u << i);
#if DEBUG_USB
    Serial.print("CRC error cell "); Serial.println(i + 1);
#endif
    return false;
  }
  return true;
}

void onAdded(int i, const byte addr[8]) {
  setSlotLevel(i, 255);
  flashRed();
  sendFrameRepeated(T_ADDED, i + 1, addr);
}

void onRemoved(int i, const byte addr[8]) {
  // Clear locally before waiting for the server to revoke the ready effect.
  breathMask &= ~(uint16_t(1) << i);
  if (hintActive && dfestaCursor && !dfestaReleasing) {
    const int column = i / 8;
    int answer = -1;
    for (int pitch = 0; pitch < 8; ++pitch)
      if (dfestaMasks[column] & (1 << pitch)) answer = pitch;
    if (answer == i % 8) {
      dfestaMasks[column] = 0;
      for (int pitch = 0; pitch < 8; ++pitch) setRing(column * 8 + pitch, 0, false);
      ringLow.show(); ringHigh.show();
    }
  }
  setSlotLevel(i, 0);
  sendFrameRepeated(T_REMOVED, i + 1, addr);
}

void scanBus(int i) {
  byte addr[8];
  bool present = readBus(i, addr);
  if (present) {
    missing[i] = false;
    if (!hasLast[i]) {
      memcpy(lastAddr[i], addr, 8);
      hasLast[i] = true;
      onAdded(i, addr);
    } else if (!sameAddr(lastAddr[i], addr)) {
      onRemoved(i, lastAddr[i]);
      memcpy(lastAddr[i], addr, 8);
      onAdded(i, addr);
    } else if (ringLevel[i] != 255) {
      setSlotLevel(i, 255);   // 접촉이 돌아왔다 — 페이드를 되돌려 다시 밝힌다
    }
  } else if (hasLast[i]) {
    // 짧은 읽기 실패로 제거를 알리면 add/remove 가 반복해서 나간다.
    // 미검출이 REMOVE_DEBOUNCE_MS 동안 이어질 때만 실제로 빠진 것으로 본다.
    if (!missing[i]) {
      missing[i] = true;
      missSinceMs[i] = millis();
      lastFadeMs[i] = 0;
      if (!hintActive && (breathMask & (uint16_t(1) << i))) setSlotLevel(i, 0);
    }
    unsigned long gone = millis() - missSinceMs[i];
    if (gone >= REMOVE_DEBOUNCE_MS) {
      onRemoved(i, lastAddr[i]);   // 여기서 밝기가 0 이 된다
      hasLast[i] = false;
      missing[i] = false;
      memset(lastAddr[i], 0, 8);
    } else if (millis() - lastFadeMs[i] >= FADE_STEP_MS) {
      // 접촉이 끊긴 순간부터 서서히 어두워져 removed 시점에 정확히 0 이 된다.
      // 손을 떼면 곧바로 반응하면서도, 튐이면 밝기만 잠깐 내려갔다 되돌아온다.
      setSlotLevel(i, (uint8_t)(255 - (255UL * gone / REMOVE_DEBOUNCE_MS)));
      lastFadeMs[i] = millis();
    }
  } else {
    missing[i] = false;
  }
}

// Do not transmit from drainBus(): csSendBytes() itself drains the bus.
void sendLedAck() {
  if (!ledAckPending) return;
  const uint8_t effect = ledAckEffect;
  const uint32_t received = ledReceivedMs;
  const uint32_t applied = ledAppliedMs;
  ledAckPending = false;
  if (usbDirect) {
    Serial.println(String("{\"type\":\"led_ack\",\"transport\":\"usb\",\"slave\":") +
      slaveId + ",\"effect\":" + effect + ",\"received_ms\":" + received +
      ",\"applied_ms\":" + applied + ",\"ack_wait_ms\":" + (uint32_t)(millis() - applied) + "}");
    return;
  }
  uint8_t frame[FRAME_LEN] = {STX, T_LED_ACK_TIMING, slaveId, effect};
  for (int i = 0; i < 4; ++i) {
    frame[4 + i] = (received >> (8 * i)) & 0xff;
  }
  uint32_t duration = applied - received;
  uint16_t bounded = duration > 65535UL ? 65535 : (uint16_t)duration;
  frame[8] = bounded & 0xff;
  frame[9] = bounded >> 8;
  frame[12] = crc8(&frame[1], 11);
  csSendBytes(frame, FRAME_LEN, applied);
}

void setup() {
  // DIP 경고를 볼 수 있어야 하므로 USB Serial 은 항상 연다.
  // (DEBUG_USB 는 셀별 CRC 로그 같은 시끄러운 출력만 켠다)
  Serial.begin(115200);
#if DEBUG_USB
  delay(500);
#endif

  pinMode(PIN_DIP_1, INPUT_PULLUP);
  pinMode(PIN_DIP_2, INPUT_PULLUP);
  pinMode(PIN_DIP_3, INPUT_PULLUP);
  delay(10);   // 내부 풀업이 안정된 뒤에 읽는다
  slaveId = (uint8_t)((!digitalRead(PIN_DIP_1))
                    | (!digitalRead(PIN_DIP_2) << 1)
                    | (!digitalRead(PIN_DIP_3) << 2));
  if (slaveId == 0) {
    Serial.println("#slave WARN: DIP 미설정 — 보드 ID 가 0 이라 서버가 무시한다");
  } else {
    Serial.print("#slave id="); Serial.println(slaveId);
  }

  led.begin();
  led.setBrightness(LED_BRIGHTNESS);
  led.clear();
  led.show();

  ringLow.begin();
  ringLow.setBrightness(RING_BRIGHTNESS);
  ringLow.clear();
  ringLow.show();

  ringHigh.begin();
  ringHigh.setBrightness(RING_BRIGHTNESS);
  ringHigh.clear();
  ringHigh.show();

  RS485.begin(RS485_BAUD, SERIAL_8N1, RX_PIN, TX_PIN);
  // DE 를 하드웨어 RTS 에 맡긴다. 송신 시작·종료에 맞춰 UART 가 직접 토글하므로
  // flush() 뒤에 수동으로 딜레이를 주거나 DE 를 내릴 필요가 없다.
  RS485.setPins(RX_PIN, TX_PIN, -1, DE_PIN);
  RS485.setMode(UART_MODE_RS485_HALF_DUPLEX);
  // C<n> commands are shorter than a typical UART FIFO threshold.
  // Expose each byte promptly even when RX-idle interrupts are ineffective.
  bool fifoReady = RS485.setRxFIFOFull(1);
  bool timeoutReady = RS485.setRxTimeout(1);
  Serial.print("#slave firmware=rs485-rx-v3 fifo1=");
  Serial.print(fifoReady ? "ok" : "FAILED");
  Serial.print(" timeout1="); Serial.println(timeoutReady ? "ok" : "FAILED");

  randomSeed(slaveId * 7919 + 13);
  lastHeartbeatMs = millis() - (unsigned long)slaveId * 300;
}

void loop() {
  pollUsb();
  drainBus();

  for (int i = 0; i < BUS_COUNT; i++) {
    scanBus(i);
    drainBus();
    updateLed();
    updateHint();
  }

  if (millis() - lastHeartbeatMs >= HEARTBEAT_MS) {
    sendHeartbeat();
    lastHeartbeatMs = millis();
  }

  runEffectAnim();   // 애니메이션 이펙트(7~9) 갱신
  updateHint();
  sendLedAck();
  updateLed();
  delay(1);
}
