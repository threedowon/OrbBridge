// Hardware boundary for test_slave_led_firmware.py. Production LED functions
// are inserted below and every show() is observed to catch one-frame flashes.
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#include <algorithm>

using byte = uint8_t;
using String = std::string;
const int NEO_GRB = 0, NEO_KHZ800 = 0;
const float PI = 3.14159265f;
uint32_t clockMs = 100;
uint32_t millis() { return clockMs; }
struct OneWire { explicit OneWire(int) {} };
struct HardwareSerial { explicit HardwareSerial(int) {} };

struct Adafruit_NeoPixel {
  std::vector<uint32_t> pixels;
  std::vector<std::vector<uint32_t>> frames;
  Adafruit_NeoPixel(int count, int, int) : pixels(count) {}
  static uint32_t Color(uint8_t r, uint8_t g, uint8_t b) {
    return uint32_t(r) << 16 | uint32_t(g) << 8 | b;
  }
  static uint32_t ColorHSV(uint16_t) { return 0; }
  void setPixelColor(int i, uint32_t color) { pixels.at(i) = color; }
  uint32_t getPixelColor(int i) const { return pixels.at(i); }
  void show() { frames.push_back(pixels); }
};

// INSERT_SLAVE_LED_FIRMWARE

void command(const char* line) {
  HintCommandParser parsed;
  assert(parsed.parse(line));
  applyHintCommand(parsed);
}

uint8_t shown(int slot) {
  const LedMap& m = LED_MAP[slot];
  return uint8_t((m.strip == 0 ? ringLow : ringHigh).getPixelColor(m.offset) >> 16);
}

void resetScene(uint32_t now = 100) {
  clockMs = now;
  serverLease.renew(now);
  slotLedsOnline = true;
  slaveId = 1;
  applyLedCommand(0);
  memset(hasLast, 0, sizeof(hasLast));
  memset(missing, 0, sizeof(missing));
  memset(ringLevel, 0, sizeof(ringLevel));
  hasLast[0] = hasLast[8] = true;
  ringLevel[0] = ringLevel[8] = 255;
  hintFrameAt = breathFrame = now - 30;
  fillAll(0);
  applyEffectOnce();
  ringLow.frames.clear(); ringHigh.frames.clear();
}

void endHint(bool readyBeforeEnd = true, unsigned fade = 600) {
  command("B110000000000");
  command("H0,0,1,600");
  command("B000000000000");
  const std::string note = "H1,1,100," + std::to_string(fade);
  command(note.c_str());
  clockMs += 100;
  const std::string end = "H0,0,3," + std::to_string(fade);
  command(end.c_str());
  if (readyBeforeEnd) command("B110000000000");
  clockMs += fade;
  updateHint();
  // The real bug: stopHint used to call applyEffectOnce here, displaying 255
  // before the breathing renderer had a chance to run.
  if (hintActive || !hintRestoring)
    fprintf(stderr, "End hint failed: fade=%u, ready=%d, now=%u, frame=%u\n",
            fade, readyBeforeEnd, clockMs, hintFrameAt);
  assert(!hintActive && hintRestoring);
  assert(shown(0) == 0 && shown(8) == 0);
}

void checkSmoothReturn(bool readyBeforeEnd, unsigned fade) {
  resetScene();
  endHint(readyBeforeEnd, fade);
  const uint32_t start = clockMs;
  int previous = 0;
  for (unsigned age = 0; age <= 600; age += 30) {
    clockMs = start + age;
    const size_t firstFrame = ringLow.frames.size();
    if (!readyBeforeEnd && age == 90) command("B110000000000");
    if (age == 300 || age == 510) command("B110000000000");
    // Scanner refreshes and repeated ready commands must not bypass the fade.
    setSlotLevel(0, 255);
    if (age % 60) updateHint();  // Inter-slot scans also service the return.
    else runEffectAnim();
    assert(shown(0) >= previous && shown(0) - previous <= 21);
    for (size_t i = firstFrame; i < ringLow.frames.size(); ++i)
      assert(int(ringLow.frames[i][0] >> 16) - previous <= 21);
    assert(shown(1) == 0 && shown(9) == 0);
    previous = shown(0);
  }
  assert(!hintRestoring && shown(0) == 255 && shown(8) == 255);
  clockMs += 30;
  runEffectAnim();
  assert(shown(0) >= 253);  // The breathing phase starts at the reached peak.
  clockMs += 970;
  command("B110000000000");
  runEffectAnim();
  assert(shown(0) == 32);
}

int main() {
  for (unsigned fade : {0U, 600U, 1200U}) {
    checkSmoothReturn(true, fade);
    checkSmoothReturn(false, fade);
  }

  // Losing the correct condition during the return uses ordinary occupancy.
  resetScene(); endHint();
  clockMs += 150; runEffectAnim();
  missing[0] = true; setSlotLevel(0, 0);
  clockMs += 30; runEffectAnim();
  assert(shown(0) == 0);
  hasLast[0] = false;
  command("B000000000000");
  clockMs += 420; runEffectAnim();
  assert(shown(0) == 0 && shown(8) == 255);

  // No ready command (wrong answer) still restores occupied rings gradually.
  resetScene(); endHint(false);
  clockMs += 300; runEffectAnim();
  assert(shown(0) >= 127 && shown(0) <= 128);
  clockMs += 300; runEffectAnim();
  assert(shown(0) == 255 && breathMask == 0);

  // A replacement LISTEN/PLAY/DFESTA must own the lights without an old fade
  // later restoring the other occupied ring or leaving the whole table dark.
  for (const char* next : {"H0,0,1,600", "P1,1,1,400", "Q1,1,7,400"}) {
    resetScene(); endHint();
    clockMs += 300; runEffectAnim();
    command(next);
    assert(!hintRestoring && hintActive);
    const auto low = ringLow.pixels, high = ringHigh.pixels;
    clockMs += 300; runEffectAnim();
    assert(low == ringLow.pixels && high == ringHigh.pixels);
  }

  resetScene(); endHint();
  clockMs += 300; runEffectAnim();
  command("H0,0,4,600");
  assert(!hintRestoring && shown(0) == 0 && shown(8) == 0);
  clockMs += 600; runEffectAnim();
  assert(shown(0) == 0);

  resetScene(); endHint();
  clockMs += 300; runEffectAnim();
  serverLease.disable(); updateServerLease();
  assert(!hintRestoring && !slotLedsOnline && shown(0) == 0);
  clockMs += 600; runEffectAnim();
  assert(shown(0) == 0 && shown(8) == 0);

  // Debug effects and a new tray session also supersede the return.
  resetScene(); endHint();
  applyLedCommand(3);
  assert(!hintRestoring && shown(0) == 255 && shown(8) == 255);
  clockMs += 600; runEffectAnim();
  assert(ringLow.getPixelColor(0) == Adafruit_NeoPixel::Color(255, 0, 0));
  resetScene(); endHint();
  command("T2000");
  assert(!hintRestoring && trayGlow && shown(0) == 0);

  // An already-blocked late DFESTA beat must not cancel an unrelated restore.
  resetScene(); endHint();
  dfestaBlocked = true;
  command("Q1,1,7,400");
  assert(hintRestoring && !hintActive);
  clockMs += 600; runEffectAnim();
  assert(!hintRestoring && shown(0) == 255);

  // Normal activation retains the already-lit orb level; no new fade to black.
  resetScene();
  command("B110000000000");
  runEffectAnim();
  assert(shown(0) == 255 && !hintRestoring);

  // millis wraparound cannot strand the return transition.
  resetScene(0xfffffd00U); endHint();
  clockMs += 300; runEffectAnim();
  assert(shown(0) >= 127 && shown(0) <= 128);
  clockMs += 300; runEffectAnim();
  assert(shown(0) == 255 && !hintRestoring);
}
