#include "capture_auth_policy.h"

namespace CaptureAuthPolicy {

bool isRegistration(CaptureMode mode) {
  return mode == CaptureMode::REGISTRATION_FIRST ||
         mode == CaptureMode::REGISTRATION_SECOND;
}

DeviceState retryState(CaptureMode mode) {
  if (mode == CaptureMode::CALIBRATION) {
    return DeviceState::CALIBRATION_READY;
  }
  if (isRegistration(mode)) {
    return DeviceState::REGISTRATION_READY;
  }
  return DeviceState::PATTERN_INPUT;
}

MessageKind patternNotEnteredMessage(CaptureMode mode) {
  return isRegistration(mode) ? MessageKind::REGISTRATION_PATTERN_NOT_ENTERED
                              : MessageKind::AUTH_PATTERN_NOT_ENTERED;
}

DeviceState inactivityExhaustedState(CaptureMode mode) {
  return mode == CaptureMode::AUTHENTICATION ? DeviceState::ID_INPUT
                                              : DeviceState::STANDBY;
}

Transition authenticationFailure(bool validPayload, int32_t remainingAttempts) {
  if (!validPayload) {
    return {MessageKind::PI_ERROR, DeviceState::PATTERN_INPUT};
  }
  if (remainingAttempts == 0) {
    return {MessageKind::NOT_USER, DeviceState::ID_INPUT};
  }
  return {MessageKind::WRONG_PATTERN, DeviceState::PATTERN_INPUT};
}

DeviceState authenticationProcessingErrorState(DeviceState state) {
  return state == DeviceState::WAIT_CAPTURE_RESULT ? DeviceState::PATTERN_INPUT
                                                    : DeviceState::ID_INPUT;
}

}  // namespace CaptureAuthPolicy
