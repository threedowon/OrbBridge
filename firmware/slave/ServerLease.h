#pragma once
#include <stdint.h>

// Only an explicit heartbeat from the PC may renew this lease. Slave traffic
// and LED commands must not keep the table lit after the server disconnects.
class ServerLease {
 public:
  static const uint32_t TIMEOUT_MS = 5000;
  void renew(uint32_t now) { renewedAt_ = now; enabled_ = true; }
  void disable() { enabled_ = false; }
  bool enabled() const { return enabled_; }
  bool online(uint32_t now) const {
    return enabled_ && age(now) < TIMEOUT_MS;
  }
  uint32_t age(uint32_t now) const { return uint32_t(now - renewedAt_); }
 private:
  uint32_t renewedAt_ = 0;
  bool enabled_ = false;
};
