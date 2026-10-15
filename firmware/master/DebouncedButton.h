#pragma once
#include <stdint.h>

// Returns true once per stable edge; holding never creates more edges.
struct DebouncedButton {
  bool raw = false;
  bool down = false;
  uint32_t changedAt = 0;

  bool update(bool pressed, uint32_t now) {
    if (pressed != raw) {
      raw = pressed;
      changedAt = now;
    }
    if (raw == down || uint32_t(now - changedAt) < 25) return false;
    down = raw;
    return true;
  }
};
