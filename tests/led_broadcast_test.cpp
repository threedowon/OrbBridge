#include "../firmware/master/LedBroadcast.h"
#include "../firmware/slave/LedCommandParser.h"

int main() {
  LedBroadcast b;
  if (b.ready(100, 0) || b.finished(100)) return 1;
  b.queue(1, 100);
  if (b.ready(100, 99) || !b.ready(101, 99)) return 2;
  b.sent(101);
  if (b.ready(115, 0) || !b.ready(116, 0)) return 3;
  if (b.ready(116, 115)) return 4;  // Traffic defers the retry.
  for (unsigned long now = 116; now <= 161; now += 15) {
    if (!b.ready(now, 0)) return 5;
    b.sent(now);
  }
  if (!b.finished(161) || b.count() != 5 || b.ready(200, 0)) return 6;
  b.stop();
  if (b.ready(200, 0)) return 7;
  b.queue(1, 200);
  b.sent(200);
  b.queue(2, 201);  // Latest desired effect supersedes old retries.
  if (b.effect() != 2 || b.count() != 0) return 8;
  if (!b.finished(1201) || b.ready(1201, 0)) return 9;
  b.queue(2, 0xfffffff0UL);  // millis rollover (32-bit unsigned long).
  if (!b.ready(2, 0xfffffffeUL) || b.finished(2)) return 10;
  // New command framing works with the existing slave parser.
  LedCommandParser parser;
  for (unsigned char effect = 0; effect <= 9; ++effect) {
    const unsigned char command[] = {'\n', 'C',
      (unsigned char)('0' + effect), '\n'};
    int decoded = -1;
    for (unsigned char byte : command) {
      int result = parser.feed(byte, 100);
      if (result >= 0) decoded = result;
    }
    if (decoded != effect) return 11;
  }
  return 0;
}
