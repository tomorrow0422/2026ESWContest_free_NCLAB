#include "controller_policy.h"

namespace ControllerPolicy {

bool canStartPressureMonitor(DeviceState state) {
  return state == DeviceState::STANDBY || state == DeviceState::ID_INPUT;
}

bool canApplyPressureConfiguration(DeviceState state, CaptureMode captureMode) {
  return state == DeviceState::STANDBY || state == DeviceState::ID_INPUT ||
         (state == DeviceState::WAIT_CAPTURE_RESULT &&
          captureMode == CaptureMode::CALIBRATION);
}

bool canStartCalibration(DeviceState state) {
  return state == DeviceState::STANDBY || state == DeviceState::ID_INPUT;
}

bool canStartRegistrationFirst(DeviceState state, bool pressureConfigured) {
  return pressureConfigured &&
         (state == DeviceState::STANDBY || state == DeviceState::ID_INPUT ||
          state == DeviceState::WAIT_CAPTURE_RESULT);
}

bool canStartRegistrationSecond(DeviceState state, bool pressureConfigured) {
  return pressureConfigured && state == DeviceState::WAIT_CAPTURE_RESULT;
}

}  // namespace ControllerPolicy
