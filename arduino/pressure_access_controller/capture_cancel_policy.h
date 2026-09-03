#pragma once

#include <stdint.h>

#include "device_types.h"

namespace CaptureCancelPolicy {

enum class Destination : uint8_t {
  NONE,
  ID_INPUT,
  STANDBY,
};

Destination userDestination(CaptureMode mode);
Destination piDestination(CaptureMode mode);

}  // namespace CaptureCancelPolicy
