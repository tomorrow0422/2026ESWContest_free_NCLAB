#include "keypad_event_tracker.h"

namespace KeypadInput {

KeyEventTracker::KeyEventTracker(uint32_t debounceMs, uint32_t longPressMs,
                                 char noKey)
    : debounceMs_(debounceMs),
      longPressMs_(longPressMs),
      noKey_(noKey),
      candidateKey_(noKey),
      stableKey_(noKey),
      candidateStartedMs_(0),
      pressStartedMs_(0),
      longPressEmitted_(false) {}

void KeyEventTracker::reset() {
  candidateKey_ = noKey_;
  stableKey_ = noKey_;
  candidateStartedMs_ = 0;
  pressStartedMs_ = 0;
  longPressEmitted_ = false;
}

bool KeyEventTracker::update(char rawKey, uint32_t nowMs, KeyEvent& event) {
  bool activityStarted = false;
  return update(rawKey, nowMs, event, activityStarted);
}

bool KeyEventTracker::update(char rawKey, uint32_t nowMs, KeyEvent& event,
                             bool& activityStarted) {
  activityStarted = false;

  if (rawKey != candidateKey_) {
    candidateKey_ = rawKey;
    candidateStartedMs_ = nowMs;
    return false;
  }

  if (nowMs - candidateStartedMs_ < debounceMs_) {
    return false;
  }

  if (stableKey_ != candidateKey_) {
    const char previousKey = stableKey_;
    stableKey_ = candidateKey_;

    if (previousKey == '#') {
      const bool released = stableKey_ == noKey_;
      const bool shouldEmitShort = released && !longPressEmitted_;
      longPressEmitted_ = false;
      pressStartedMs_ = 0;
      if (shouldEmitShort) {
        event = KeyEvent{'#', KeyEventType::SHORT_PRESS};
        return true;
      }
    }

    if (stableKey_ == '#') {
      activityStarted = true;
      pressStartedMs_ = nowMs;
      longPressEmitted_ = false;
      return false;
    }

    if (stableKey_ != noKey_) {
      activityStarted = true;
      event = KeyEvent{stableKey_, KeyEventType::PRESS};
      return true;
    }
    return false;
  }

  if (stableKey_ == '#' && !longPressEmitted_ &&
      nowMs - pressStartedMs_ >= longPressMs_) {
    longPressEmitted_ = true;
    event = KeyEvent{'#', KeyEventType::LONG_PRESS};
    return true;
  }

  return false;
}

}  // namespace KeypadInput
