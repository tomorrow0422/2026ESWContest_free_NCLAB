#pragma once

#include <stdint.h>

namespace KeypadInput {

enum class KeyEventType : uint8_t {
  PRESS,
  SHORT_PRESS,
  LONG_PRESS,
};

struct KeyEvent {
  char key;
  KeyEventType type;
};

class KeyEventTracker {
 public:
  KeyEventTracker(uint32_t debounceMs, uint32_t longPressMs, char noKey);

  void reset();
  bool update(char rawKey, uint32_t nowMs, KeyEvent& event);
  bool update(char rawKey, uint32_t nowMs, KeyEvent& event,
              bool& activityStarted);

 private:
  uint32_t debounceMs_;
  uint32_t longPressMs_;
  char noKey_;
  char candidateKey_;
  char stableKey_;
  uint32_t candidateStartedMs_;
  uint32_t pressStartedMs_;
  bool longPressEmitted_;
};

}  // namespace KeypadInput
