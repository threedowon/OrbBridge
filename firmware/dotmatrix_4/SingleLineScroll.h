#pragma once
#include <stdint.h>

// Full-width, one-line titles. Hold both ends so the first/last words are legible.
class SingleLineScroll {
 public:
  static const uint32_t HOLD_MS = 1000;
  static const uint32_t PIXEL_MS = 35;

  void begin(int textWidth, int viewWidth, uint32_t now) {
    textWidth_ = textWidth;
    viewWidth_ = viewWidth;
    startedAt_ = now;
  }
  void stop() { textWidth_ = viewWidth_ = 0; }
  bool active() const { return textWidth_ > viewWidth_; }
  int x(uint32_t now) const {
    if (!active()) return (viewWidth_ - textWidth_) / 2;
    const uint32_t distance = uint32_t(textWidth_ - viewWidth_);
    const uint32_t phase = uint32_t(now - startedAt_) % (2 * HOLD_MS + distance * PIXEL_MS);
    if (phase < HOLD_MS) return 0;
    const uint32_t moved = (phase - HOLD_MS) / PIXEL_MS;
    return -int(moved < distance ? moved : distance);
  }

 private:
  int textWidth_ = 0, viewWidth_ = 0;
  uint32_t startedAt_ = 0;
};
