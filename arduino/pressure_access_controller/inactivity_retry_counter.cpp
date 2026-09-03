#include "inactivity_retry_counter.h"

InactivityRetryCounter::InactivityRetryCounter(uint8_t maxAttempts)
    : maxAttempts_(maxAttempts == 0 ? 1 : maxAttempts) {}

void InactivityRetryCounter::reset() {
  attempts_ = 0;
}

InactivityAttemptResult InactivityRetryCounter::recordTimeout() {
  if (attempts_ < maxAttempts_) {
    ++attempts_;
  }
  return InactivityAttemptResult{
      attempts_ >= maxAttempts_,
      static_cast<uint8_t>(maxAttempts_ - attempts_),
  };
}
