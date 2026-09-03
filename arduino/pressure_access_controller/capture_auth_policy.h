#pragma once

#include "device_types.h"

namespace CaptureAuthPolicy {

struct Transition {
  MessageKind message;
  DeviceState nextState;
};

bool isRegistration(CaptureMode mode);
DeviceState retryState(CaptureMode mode);
MessageKind patternNotEnteredMessage(CaptureMode mode);
DeviceState inactivityExhaustedState(CaptureMode mode);
Transition authenticationFailure(bool validPayload, int32_t remainingAttempts);
DeviceState authenticationProcessingErrorState(DeviceState state);

}  // namespace CaptureAuthPolicy
