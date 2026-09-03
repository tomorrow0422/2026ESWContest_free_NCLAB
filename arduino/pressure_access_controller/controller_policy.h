#pragma once

#include "device_types.h"

namespace ControllerPolicy {

bool canStartPressureMonitor(DeviceState state);
bool canApplyPressureConfiguration(DeviceState state, CaptureMode captureMode);
bool canStartCalibration(DeviceState state);
bool canStartRegistrationFirst(DeviceState state, bool pressureConfigured);
bool canStartRegistrationSecond(DeviceState state, bool pressureConfigured);

}  // namespace ControllerPolicy
