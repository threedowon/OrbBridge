#pragma once

// RS485 carries both 13-byte slave frames and C<digit>\n master commands.
// Never interpret a UID or CRC byte as part of a text command.
class LedCommandParser {
 public:
  int feed(unsigned char c, unsigned long now) {
    if (now - lastByteMs_ > 20) reset();
    lastByteMs_ = now;
    if (binaryRemaining_) {
      --binaryRemaining_;
      return -1;
    }
    if (c == 0x02) {
      reset();
      binaryRemaining_ = 12;
      return -1;
    }
    if (c == '\n') {
      int result = state_ == 2 ? effect_ : -1;
      reset();
      return result;
    }
    if (c == '\r' && state_ == 2) return -1;
    if (state_ == 0 && c == 'C') {
      state_ = 1;
    } else if (state_ == 1 && c >= '0' && c <= '9') {
      effect_ = c - '0';
      state_ = 2;
    } else {
      state_ = 3;  // Invalid line: wait for a newline or a binary frame.
    }
    return -1;
  }

 private:
  void reset() { state_ = 0; binaryRemaining_ = 0; }
  unsigned char state_ = 0;
  unsigned char binaryRemaining_ = 0;
  int effect_ = 0;
  unsigned long lastByteMs_ = 0;
};
