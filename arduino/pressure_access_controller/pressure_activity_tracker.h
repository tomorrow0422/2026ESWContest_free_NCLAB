#pragma once

#include <stdint.h>

class PressureActivityTracker {
 public:
  bool configure(uint16_t activeAdc, uint8_t consecutiveSamples);
  void start(uint32_t nowMs);
  void stop();
  void observe(uint16_t adc, uint32_t nowMs);

  bool hasDetectedActivity() const;
  bool hasTimedOut(uint32_t nowMs, uint32_t timeoutMs) const;

 private:
  uint16_t activeAdc_ = 0;
  uint8_t requiredConsecutiveSamples_ = 0;
  uint8_t consecutiveSamples_ = 0;
  uint32_t lastActivityMs_ = 0;
  bool configured_ = false;
  bool started_ = false;
  bool detectedActivity_ = false;
};
