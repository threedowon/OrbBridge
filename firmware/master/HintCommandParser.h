#pragma once
#include <stdint.h>
#include <stdio.h>

// H<slave>,<cell>,<duration_ms>\n; H0,0,0 restores normal display.
// H0,0,1 starts a blank session; H0,0,2 preserves tails; H0,0,3 finishes after fading.
// Binary frames on the shared RS485 bus are skipped, including embedded newlines.
class HintCommandParser {
 public:
  uint8_t slave = 0, cell = 0;
  uint16_t duration = 0;
  uint16_t fadeMs = 600;
  bool playback = false;
  bool tray = false;
  bool breathing = false;
  char breathColumns[13] = {};
  bool dfesta = false;
  uint8_t mask = 0;
  bool parse(const char* line) {
    if (line[0] == 'B') {
      unsigned n = 0;
      while (line[n + 1]) {
        if (n >= 12 || line[n + 1] < '0' || line[n + 1] > '8') return false;
        ++n;
      }
      if (n != 12) return false;
      for (unsigned i = 0; i < 12; ++i) breathColumns[i] = line[i + 1];
      breathColumns[12] = 0; breathing = true;
      return true;
    }
    breathing = false;
    if (line[0] != 'H' && line[0] != 'P' && line[0] != 'T' && line[0] != 'Q') return false;
    for (const char* p = line + 1; *p; ++p)
      if ((*p < '0' || *p > '9') && *p != ',') return false;
    unsigned s, c, d, m, fade = 600; char extra;
    if (line[0] == 'T') {
      if (sscanf(line, "T%u%c", &d, &extra) != 1 || d < 1 || d > 60000) return false;
      slave = 0; cell = 0; duration = d; playback = false; tray = true; dfesta = false;
      return true;
    }
    if (line[0] == 'P' || line[0] == 'Q') {
      if (sscanf(line + 1, "%u,%u,%u,%u%c", &s, &c, &m, &d, &extra) != 4) return false;
      if (s < 1 || s > 6 || c < 1 || c > 2 || m > 255 || d < 1 || d > (line[0] == 'Q' ? 60000U : 2000U)) return false;
      slave = s; cell = c; mask = m; duration = d; playback = true; tray = false; dfesta = line[0] == 'Q';
      return true;
    }
    const int fields = sscanf(line, "H%u,%u,%u,%u%c", &s, &c, &d, &fade, &extra);
    if (fields != 4 && sscanf(line, "H%u,%u,%u%c", &s, &c, &d, &extra) != 3) return false;
    if (fade > 3000) return false;
    if (!((s == 0 && c == 0 && d <= 2000) ||
          (s >= 1 && s <= 6 && c >= 1 && c <= 16 && d >= 1 && d <= 2000))) return false;
    slave = s; cell = c; duration = d; playback = false; tray = false; dfesta = false; mask = 0; fadeMs = fade;
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
