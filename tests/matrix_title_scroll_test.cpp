#include "../firmware/dotmatrix_3/SingleLineScroll.h"
#define SingleLineScroll OtherSingleLineScroll
#include "../firmware/dotmatrix_4/SingleLineScroll.h"
#undef SingleLineScroll
#include <assert.h>

template <typename Scroll> void checkScroll() {
  Scroll scroll;
  assert(!scroll.active());
  scroll.begin(31 * 6 - 1, 192, 0);
  assert(!scroll.active() && scroll.x(0) == 3 && scroll.x(10000) == 3);
  scroll.begin(32 * 6 - 1, 192, 0);
  assert(!scroll.active() && scroll.x(0) == 0);
  scroll.begin(33 * 6 - 1, 192, 0);  // 197px: reveal the final five pixels.
  assert(scroll.active());
  assert(scroll.x(0) == 0 && scroll.x(1000) == 0);
  assert(scroll.x(1035) == -1);
  assert(scroll.x(1175) == -5 && scroll.x(2174) == -5);
  assert(scroll.x(2175) == 0);  // Loop after holding the final word.
  scroll.begin(33 * 6 - 1, 192, 10000);
  assert(scroll.x(10000) == 0 && scroll.x(11035) == -1);
  scroll.stop();
  assert(!scroll.active());
  scroll.begin(48 * 4 - 1, 192, 0);  // Small font fits 48 chars.
  assert(!scroll.active());
  scroll.begin(49 * 4 - 1, 192, 0);
  assert(scroll.active() && scroll.x(1105) == -3);
  scroll.begin(33 * 6 - 1, 192, 0xfffffff0U);
  assert(scroll.x(uint32_t(0xfffffff0U + 1035U)) == -1);
}

int main() {
  checkScroll<SingleLineScroll>();
  checkScroll<OtherSingleLineScroll>();
}
