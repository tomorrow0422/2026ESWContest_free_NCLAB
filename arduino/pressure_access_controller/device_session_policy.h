#pragma once

#include "device_types.h"

namespace DeviceSessionPolicy {

DeviceProtocolMode protocolMode(DeviceState state, CaptureMode captureMode,
                                DeviceState stateAfterMessage);

}  // namespace DeviceSessionPolicy
