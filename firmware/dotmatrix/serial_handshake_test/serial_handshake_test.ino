/*
 * OrbBridge Dot Matrix ESP32-S3 — USB handshake 진단용
 *
 * HUB75 렌더링을 넣기 전, Python의 자동 COM 식별·ping·명령 수신만 확인한다.
 * 최종 표시 펌웨어에서는 이 USB 처리 함수를 그대로 합치고 show/clear/brightness
 * 분기만 실제 렌더 함수에 연결하면 된다.
 */

#include <Arduino.h>

#define USB_PROTOCOL "orbbridge"
#define USB_PROTOCOL_VERSION 1
#define FW_VERSION "0.1.0-handshake"

String usbInputLine;
String dotmatrixDeviceId;

String makeDeviceId() {
  uint64_t mac = ESP.getEfuseMac();
  char value[13];
  snprintf(
    value,
    sizeof(value),
    "%04X%08X",
    (uint16_t)(mac >> 32),
    (uint32_t)mac
  );
  return String(value);
}

String jsonStringValue(const String& json, const char* key) {
  String token = String("\"") + key + "\"";
  int keyAt = json.indexOf(token);
  if (keyAt < 0) return "";
  int colonAt = json.indexOf(':', keyAt + token.length());
  if (colonAt < 0) return "";
  int valueAt = json.indexOf('"', colonAt + 1);
  if (valueAt < 0) return "";
  int endAt = json.indexOf('"', valueAt + 1);
  if (endAt < 0) return "";
  return json.substring(valueAt + 1, endAt);
}

void sendHello(const String& nonce) {
  Serial.print("{\"type\":\"hello\",\"protocol\":\"");
  Serial.print(USB_PROTOCOL);
  Serial.print("\",\"version\":");
  Serial.print(USB_PROTOCOL_VERSION);
  Serial.print(",\"role\":\"dotmatrix\",\"device_id\":\"");
  Serial.print(dotmatrixDeviceId);
  Serial.print("\",\"fw\":\"");
  Serial.print(FW_VERSION);
  Serial.print("\",\"nonce\":\"");
  Serial.print(nonce);
  Serial.println("\"}");
}

void handleUsbCommand(const String& line) {
  String cmd = jsonStringValue(line, "cmd");
  if (cmd == "hello") {
    sendHello(jsonStringValue(line, "nonce"));
  } else if (cmd == "ping") {
    Serial.println("{\"type\":\"pong\"}");
  } else if (cmd == "show" || cmd == "clear" || cmd == "brightness") {
    // 진단 스케치는 명령 수신만 확인한다. 최종 펌웨어에서 렌더 함수에 연결한다.
  }
}

void pollUsbCommands() {
  while (Serial.available()) {
    char ch = (char)Serial.read();
    if (ch == '\n') {
      usbInputLine.trim();
      if (usbInputLine.length() > 0) handleUsbCommand(usbInputLine);
      usbInputLine = "";
    } else if (ch != '\r' && usbInputLine.length() < 512) {
      usbInputLine += ch;
    }
  }
}

void setup() {
  Serial.begin(115200);
  usbInputLine.reserve(256);
  dotmatrixDeviceId = makeDeviceId();
}

void loop() {
  pollUsbCommands();
  delay(1);
}
