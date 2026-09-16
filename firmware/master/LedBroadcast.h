#pragma once

// Retries run in loop(), keeping reception and button handling responsive.
class LedBroadcast {
 public:
  void queue(unsigned char effect, unsigned long now) {
    effect_ = effect;
    sent_ = 0;
    started_ = lastSent_ = now;
    active_ = true;
  }
  bool ready(unsigned long now, unsigned long lastRx) const {
    return active_ && sent_ < 5 && !expired(now) && now - lastRx >= 2 &&
           (sent_ == 0 || now - lastSent_ >= 15);
  }
  void sent(unsigned long now) { ++sent_; lastSent_ = now; }
  bool expired(unsigned long now) const { return now - started_ >= 1000; }
  bool finished(unsigned long now) const {
    return active_ && (sent_ >= 5 || expired(now));
  }
  void stop() { active_ = false; }
  unsigned char effect() const { return effect_; }
  unsigned char count() const { return sent_; }
 private:
  bool active_ = false;
  unsigned char effect_ = 0;
  unsigned char sent_ = 0;
  unsigned long started_ = 0;
  unsigned long lastSent_ = 0;
};
