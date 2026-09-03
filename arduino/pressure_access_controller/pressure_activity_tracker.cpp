#include "pressure_activity_tracker.h"

bool PressureActivityTracker::configure(uint16_t activeAdc,
                                        uint8_t consecutiveSamples) {
  if (activeAdc == 0 || activeAdc > 16383 || consecutiveSamples == 0) {
    return false;
  }

  activeAdc_ = activeAdc;
  requiredConsecutiveSamples_ = consecutiveSamples;
  configured_ = true;
  return true;
}

void PressureActivityTracker::start(uint32_t nowMs) {
  consecutiveSamples_ = 0;
  lastActivityMs_ = nowMs;
  started_ = true;
  detectedActivity_ = false;
}

void PressureActivityTracker::stop() {
  consecutiveSamples_ = 0;
  lastActivityMs_ = 0;
  started_ = false;
  detectedActivity_ = false;
}

void PressureActivityTracker::observe(uint16_t adc, uint32_t nowMs) {
  if (!configured_ || !started_) {
    return;
  }

  if (adc < activeAdc_) {
    consecutiveSamples_ = 0;
    return;
  }

  if (consecutiveSamples_ < requiredConsecutiveSamples_) {
    ++consecutiveSamples_;
  }
  if (consecutiveSamples_ >= requiredConsecutiveSamples_) {
    detectedActivity_ = true;
    lastActivityMs_ = nowMs;
  }
}

bool PressureActivityTracker::hasDetectedActivity() const {
  return detectedActivity_;
}

bool PressureActivityTracker::hasTimedOut(uint32_t nowMs,
                                          uint32_t timeoutMs) const {
  return started_ && nowMs - lastActivityMs_ >= timeoutMs;
}
