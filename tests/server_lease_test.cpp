#include "../firmware/master/ServerLease.h"
#include "../firmware/master/HintCommandParser.h"
#define ServerLease SlaveServerLease
#include "../firmware/slave/ServerLease.h"
#undef ServerLease
#define HintCommandParser SlaveHintCommandParser
#include "../firmware/slave/HintCommandParser.h"
#undef HintCommandParser
#include <assert.h>

template <typename Lease, typename Parser> void checkLease() {
  Lease lease;
  Parser parser;
  assert(!lease.online(0));  // Eggs present at boot cannot light until PC connects.
  lease.renew(100);
  assert(lease.online(5099));
  assert(!lease.online(5100));
  for (uint32_t now = 1000; now <= 10000; now += 1000) {
    lease.renew(now);
    assert(lease.online(now + 999));
  }
  // Ordinary lighting commands are not server heartbeats.
  const char* cues[] = {"B123456781234", "T3000,0", "H1,1,500", "P1,1,4,300", "Q1,1,7,500"};
  for (const char* cue : cues) {
    assert(parser.parse(cue));
    assert(parser.lease == -1);
  }
  assert(!lease.online(15000));  // Crash / USB / RS485 unplug, even with eggs.
  assert(parser.parse("L1") && parser.lease == 1);
  lease.renew(16000);
  assert(lease.online(16000));
  assert(parser.parse("L0") && parser.lease == 0);
  lease.disable();
  assert(!lease.online(16001));  // Graceful stop does not wait five seconds.
  lease.renew(0xfffffff0U);
  assert(lease.online(0x10U));
  assert(lease.online(4983));
  assert(!lease.online(4984));  // 32-bit millis rollover.

  const char* invalid[] = {"L", "L2", "L10", "L1x", "L1,0", "xL1"};
  for (const char* line : invalid) assert(!parser.parse(line));
  // Binary heartbeat/UID bytes that happen to contain L1 must never renew.
  const uint8_t frame[13] = {2, 3, 1, 0, 'L', '1', '\n', 'L', '0', '\n', 0, 0, 0};
  for (uint8_t byte : frame) assert(!parser.feed(byte, 20000));
  assert(!parser.feed('\n', 20000));
  assert(!parser.feed('L', 20001));
  assert(!parser.feed('1', 20002));
  assert(parser.feed('\n', 20003) && parser.lease == 1);
  assert(!parser.feed('L', 20004));
  assert(!parser.feed('0', 20005));
  assert(parser.feed('\n', 20006) && parser.lease == 0);
  assert(!parser.feed('L', 20007));
  assert(!parser.feed('1', 20050));  // Truncated / timed out command.
  assert(!parser.feed('\n', 20051));
}

int main() {
  checkLease<ServerLease, HintCommandParser>();
  checkLease<SlaveServerLease, SlaveHintCommandParser>();
}
