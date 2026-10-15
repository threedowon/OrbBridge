// Master PCB J7 -> button PCB J1: 1 GND, 2 LED DATA, 3 EX_5V, 4 BTN.
// ESP32-S3-DevKitC-1: GPIO7 = Play input; GPIO4 = Btn1_LED.
// Hold any button switch: all 8 WS2812B LEDs green. Release: off.
#include <Adafruit_NeoPixel.h>

constexpr uint8_t BUTTON_PIN = 7;
constexpr uint8_t LED_PIN = 4;
constexpr uint16_t LED_COUNT = 8;
constexpr uint32_t DEBOUNCE_MS = 25;
Adafruit_NeoPixel pixels(LED_COUNT, LED_PIN, NEO_GRB + NEO_KHZ800);

bool rawPressed = false;
bool stablePressed = false;
bool stateKnown = false;
uint32_t changedAt = 0;
uint32_t pressCount = 0;
uint32_t lastReport = 0;

void setup() {
  // Keep the unused RS485 driver disabled during this standalone test.
  digitalWrite(42, LOW);
  pinMode(42, OUTPUT);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  Serial.begin(115200);
  pixels.begin();
  pixels.setBrightness(32);  // Low brightness for first board test.
  pixels.clear();
  pixels.show();
  rawPressed = digitalRead(BUTTON_PIN) == LOW;
  changedAt = millis();
  Serial.println("PLAY test: GPIO7 input / GPIO4 LED / 8 pixels");
}

void loop() {
  const uint32_t now = millis();
  const bool pressed = digitalRead(BUTTON_PIN) == LOW;
  if (pressed != rawPressed) {
    rawPressed = pressed;
    changedAt = now;
  }
  if (now - changedAt >= DEBOUNCE_MS &&
      (!stateKnown || rawPressed != stablePressed)) {
    stablePressed = rawPressed;
    stateKnown = true;
    if (stablePressed) ++pressCount;
    pixels.fill(stablePressed ? pixels.Color(0, 255, 0) : 0);
    pixels.show();
    Serial.printf("%s | LED %s | count=%lu\n",
                  stablePressed ? "PRESSED" : "RELEASED",
                  stablePressed ? "ON" : "OFF", (unsigned long)pressCount);
  }
  // Periodic status remains visible if the monitor opens after boot.
  if (now - lastReport >= 1000) {
    lastReport = now;
    Serial.printf("raw=%s stable=%s count=%lu\n",
                  rawPressed ? "LOW" : "HIGH",
                  !stateKnown ? "WAIT" : (stablePressed ? "PRESSED" : "RELEASED"),
                  (unsigned long)pressCount);
  }
  delay(1);
}
