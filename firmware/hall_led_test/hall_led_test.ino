// Standalone Master PCB Hall test. Uses the Play button PCB's 8 LEDs.
// Hall 1 = GPIO5, Hall 2 = GPIO6, Play LED data = GPIO4.
// Sensor 1: red; sensor 2: blue; both: purple; neither: off.
#include <Adafruit_NeoPixel.h>

constexpr uint8_t HALL_1_PIN = 5;
constexpr uint8_t HALL_2_PIN = 6;
constexpr bool HALL_1_ACTIVE_LOW = true;
constexpr bool HALL_2_ACTIVE_LOW = true;
constexpr uint8_t LED_PIN = 4;
constexpr uint16_t LED_COUNT = 8;
constexpr uint32_t DEBOUNCE_MS = 30;

Adafruit_NeoPixel pixels(LED_COUNT, LED_PIN, NEO_GRB + NEO_KHZ800);
const uint8_t hallPins[2] = {HALL_1_PIN, HALL_2_PIN};
const bool activeLow[2] = {HALL_1_ACTIVE_LOW, HALL_2_ACTIVE_LOW};
bool raw[2] = {false, false};
bool stable[2] = {false, false};
bool known[2] = {false, false};
uint32_t changedAt[2] = {0, 0};
uint32_t lastReport = 0;
uint8_t shownState = 255;

void setup() {
  digitalWrite(42, LOW);
  pinMode(42, OUTPUT);  // Disable unused RS485 transmitter.
  Serial.begin(115200);
  for (uint8_t i = 0; i < 2; ++i) {
    pinMode(hallPins[i], INPUT_PULLUP);
    raw[i] = digitalRead(hallPins[i]) == (activeLow[i] ? LOW : HIGH);
    changedAt[i] = millis();
  }
  pixels.begin();
  pixels.setBrightness(32);
  pixels.clear();
  pixels.show();
  Serial.println("Hall test: H1 GPIO5 / H2 GPIO6 / LED GPIO4");
}

void loop() {
  const uint32_t now = millis();
  // Debounce independently so one noisy sensor cannot block the other.
  for (uint8_t i = 0; i < 2; ++i) {
    const bool detected = digitalRead(hallPins[i]) == (activeLow[i] ? LOW : HIGH);
    if (detected != raw[i]) {
      raw[i] = detected;
      changedAt[i] = now;
    }
    if (now - changedAt[i] >= DEBOUNCE_MS) {
      stable[i] = raw[i];
      known[i] = true;
    }
  }

  const uint8_t state = (known[0] && stable[0] ? 1 : 0) |
                        (known[1] && stable[1] ? 2 : 0);
  const bool changed = state != shownState;
  if (changed) {
    pixels.fill(pixels.Color((state & 1) ? 255 : 0, 0,
                             (state & 2) ? 255 : 0));
    pixels.show();
    shownState = state;
  }
  if (changed || now - lastReport >= 1000) {
    lastReport = now;
    const char* colors[] = {"OFF", "RED", "BLUE", "PURPLE"};
    Serial.printf("H1=%s H2=%s | rawGPIO5=%d rawGPIO6=%d | LED=%s\n",
                  !known[0] ? "WAIT" : (stable[0] ? "DETECTED" : "CLEAR"),
                  !known[1] ? "WAIT" : (stable[1] ? "DETECTED" : "CLEAR"),
                  digitalRead(HALL_1_PIN), digitalRead(HALL_2_PIN), colors[state]);
  }
  delay(1);
}
