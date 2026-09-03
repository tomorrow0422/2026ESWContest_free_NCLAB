#include "id_input_policy.h"

namespace IdInputPolicy {

HashAction hashAction(KeypadInput::KeyEventType eventType, uint8_t idLength) {
  if (eventType == KeypadInput::KeyEventType::SHORT_PRESS) {
    return idLength == 0 ? HashAction::NONE : HashAction::DELETE_LAST;
  }
  if (eventType == KeypadInput::KeyEventType::LONG_PRESS) {
    return idLength == 0 ? HashAction::ENTER_STANDBY : HashAction::CLEAR_ALL;
  }
  return HashAction::NONE;
}

bool hasTimedOut(uint32_t nowMs, uint32_t lastActivityMs,
                 uint32_t timeoutMs) {
  return nowMs - lastActivityMs >= timeoutMs;
}

}  // namespace IdInputPolicy
