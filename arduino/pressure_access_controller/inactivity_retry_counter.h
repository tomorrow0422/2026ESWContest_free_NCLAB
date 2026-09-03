#pragma once

#include <stdint.h>

struct InactivityAttemptResult {
  bool exhausted;
  uint8_t remainingAttempts;
};

class InactivityRetryCounter {
 public:
  explicit InactivityRetryCounter(uint8_t maxAttempts);

  void reset();
  InactivityAttemptResult recordTimeout();

 private:
  uint8_t maxAttempts_;
  uint8_t attempts_ = 0;
};
