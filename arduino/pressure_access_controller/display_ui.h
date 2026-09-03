#pragma once

#include <Arduino.h>

#include "device_types.h"

namespace DisplayUi {

void begin();
void sleep();
void showIdInput(const char* userId, uint8_t userIdLength,
                 bool cursorVisible);
void showPatternInput(CaptureMode mode);
void showRegistrationReady(CaptureMode mode);
void showCalibrationReady();
void showIdWait();
void showCaptureResultWait(CaptureMode mode);
void showAuthenticationSuccess();
void showMessage(MessageKind message);

}  // namespace DisplayUi
