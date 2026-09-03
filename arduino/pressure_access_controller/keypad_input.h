#pragma once

#include "keypad_event_tracker.h"

namespace KeypadInput {

void begin();
bool poll(KeyEvent& event, bool& activityStarted);

}  // namespace KeypadInput
