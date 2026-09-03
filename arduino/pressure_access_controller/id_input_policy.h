#pragma once

#include <stdint.h>

#include "keypad_event_tracker.h"

namespace IdInputPolicy {

enum class HashAction : uint8_t {
  NONE,
  DELETE_LAST,
  CLEAR_ALL,
  ENTER_STANDBY,
};

HashAction hashAction(KeypadInput::KeyEventType eventType, uint8_t idLength);
bool hasTimedOut(uint32_t nowMs, uint32_t lastActivityMs,
                 uint32_t timeoutMs);

}  // namespace IdInputPolicy
