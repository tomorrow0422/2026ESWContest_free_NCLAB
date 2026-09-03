#include "device_session_policy.h"

namespace {

DeviceProtocolMode captureProtocolMode(CaptureMode captureMode) {
  switch (captureMode) {
    case CaptureMode::AUTHENTICATION:
      return DeviceProtocolMode::AUTHENTICATION;
    case CaptureMode::REGISTRATION_FIRST:
      return DeviceProtocolMode::REGISTRATION_FIRST;
    case CaptureMode::REGISTRATION_SECOND:
      return DeviceProtocolMode::REGISTRATION_SECOND;
    case CaptureMode::CALIBRATION:
      return DeviceProtocolMode::CALIBRATION;
    case CaptureMode::NONE:
      return DeviceProtocolMode::IDLE;
  }
  return DeviceProtocolMode::IDLE;
}

}  // namespace

namespace DeviceSessionPolicy {

DeviceProtocolMode protocolMode(DeviceState state, CaptureMode captureMode,
                                DeviceState stateAfterMessage) {
  if (state == DeviceState::AUTH_SUCCESS) {
    return DeviceProtocolMode::AUTH_SUCCESS;
  }
  if (state == DeviceState::PRESSURE_MONITOR) {
    return DeviceProtocolMode::PRESSURE_MONITOR;
  }
  if (state == DeviceState::ID_INPUT ||
      state == DeviceState::WAIT_ID_RESULT) {
    return DeviceProtocolMode::AUTHENTICATION;
  }
  if (state == DeviceState::STANDBY) {
    return DeviceProtocolMode::IDLE;
  }

  const DeviceProtocolMode captureModeResult =
      captureProtocolMode(captureMode);
  if (captureModeResult != DeviceProtocolMode::IDLE) {
    return captureModeResult;
  }

  if (state == DeviceState::TIMED_MESSAGE &&
      (stateAfterMessage == DeviceState::ID_INPUT ||
       stateAfterMessage == DeviceState::WAIT_ID_RESULT)) {
    return DeviceProtocolMode::AUTHENTICATION;
  }
  return DeviceProtocolMode::IDLE;
}

}  // namespace DeviceSessionPolicy
