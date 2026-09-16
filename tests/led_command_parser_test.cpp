#include "../firmware/slave/LedCommandParser.h"

int main() {
  LedCommandParser parser;
  unsigned long now = 100;
  int count = 0;
  int last = -1;
  auto feed = [&](const unsigned char* bytes, int size) {
    for (int i = 0; i < size; ++i) {
      int effect = parser.feed(bytes[i], now);
      if (effect >= 0) { ++count; last = effect; }
    }
  };
  const unsigned char white[] = {'C', '1', '\n'};
  const unsigned char off[] = {'C', '2', '\n'};
  feed(white, 3);
  if (count != 1 || last != 1) return 1;

  // A full binary frame without a newline must not swallow the following OFF.
  const unsigned char heartbeat[] = {2, 3, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 99};
  feed(heartbeat, 13);
  feed(off, 3);
  if (count != 2 || last != 2) return 2;

  // Commands embedded in an orb UID must never turn a blacked-out ring on.
  const unsigned char uid[] = {2, 1, 1, 1, '\n', 'C', '1', '\n', 2, 0, 0, 0, 99};
  feed(uid, 13);
  if (count != 2 || last != 2) return 3;

  const unsigned char invalid[] = "C22\nCx\nC\nC-1\nC2junk\n";
  feed(invalid, sizeof(invalid) - 1);
  if (count != 2) return 4;
  for (int i = 0; i < 10; ++i) {
    const unsigned char command[] = {'C', (unsigned char)('0' + i), '\r', '\n'};
    feed(command, 4);
    if (last != i || count != 3 + i) return 5;
  }

  // An incomplete/collided binary frame expires before a later button press.
  parser.feed(2, now);
  parser.feed(3, now);
  now += 21;
  feed(off, 3);
  if (count != 13 || last != 2) return 6;
  for (int i = 0; i < 5; ++i) feed(off, 3);
  if (count != 18 || last != 2) return 7;

  // Every possible UID/CRC byte, including STX, C and newline, stays binary.
  for (int value = 0; value < 256; ++value) {
    unsigned char frame[13] = {2, 1, 1, 1};
    for (int i = 4; i < 13; ++i) frame[i] = (unsigned char)value;
    int before = count;
    feed(frame, 13);
    if (count != before) return 8;
    feed(off, 3);
    if (count != before + 1 || last != 2) return 9;
  }
  return 0;
}
