#pragma once
#include <stdint.h>
#include <stdio.h>

// H<slave>,<cell>,<duration_ms>\n; H0,0,0 restores normal display.
// H0,0,1 blanks rings while keeping the hint session active.
// Binary frames on the shared RS485 bus are skipped, including embedded newlines.
class HintCommandParser {
 public:
  uint8_t slave = 0, cell = 0;
  uint16_t duration = 0;
  bool playback = false;
  uint8_t mask = 0;
  bool parse(const char* line) {
    if (line[0] != 'H' && line[0] != 'P') return false;
    for (const char* p = line + 1; *p; ++p)
      if ((*p < '0' || *p > '9') && *p != ',') return false;
    unsigned s, c, d, m; char extra;
    if (line[0] == 'P') {
      if (sscanf(line, "P%u,%u,%u,%u%c", &s, &c, &m, &d, &extra) != 4) return false;
      if (s < 1 || s > 6 || c < 1 || c > 2 || m > 255 || d < 1 || d > 2000) return false;
      slave = s; cell = c; mask = m; duration = d; playback = true;
      return true;
    }
    if (sscanf(line, "H%u,%u,%u%c", &s, &c, &d, &extra) != 3) return false;
    if (!((s == 0 && c == 0 && d <= 2000) ||
          (s >= 1 && s <= 6 && c >= 1 && c <= 16 && d >= 1 && d <= 2000))) return false;
    slave = s; cell = c; duration = d; playback = false; mask = 0;
    return true;
  }
  bool feed(uint8_t c, uint32_t now) {
    if (uint32_t(now - lastByte) > 20) { length = 0; skip = 0; invalid = false; }
    lastByte = now;
    if (skip) { --skip; return false; }
    if (c == 2) { length = 0; invalid = false; skip = 12; return false; }
    if (c == '\n') {
      line[length] = 0;
      bool ready = !invalid && parse(line);
      length = 0; invalid = false;
      return ready;
    }
    if (c == '\r') return false;
    if (length < sizeof(line) - 1) line[length++] = c;
    else invalid = true;
    return false;
  }
 private:
  char line[24] = {};
  uint8_t length = 0, skip = 0;
  bool invalid = false;
  uint32_t lastByte = 0;
};
