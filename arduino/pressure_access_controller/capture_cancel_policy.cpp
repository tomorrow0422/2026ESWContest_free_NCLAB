#include "capture_cancel_policy.h"

namespace CaptureCancelPolicy {

Destination userDestination(CaptureMode mode) {
  if (mode == CaptureMode::AUTHENTICATION) {
    return Destination::ID_INPUT;
  }
  if (mode == CaptureMode::REGISTRATION_FIRST ||
      mode == CaptureMode::REGISTRATION_SECOND) {
    return Destination::STANDBY;
  }
  return Destination::NONE;
}

Destination piDestination(CaptureMode mode) {
  (void)mode;
  return Destination::STANDBY;
}

}  // namespace CaptureCancelPolicy
