#pragma once

#include <stdint.h>

class PressureConfiguration {
 public:
  static constexpr uint16_t MAX_ACTIVE_ADC = 16383;

  bool configure(uint16_t activeAdc, uint8_t consecutiveSamples);
  void clear();

  bool isConfigured() const;
  uint16_t activeAdc() const;
  uint8_t consecutiveSamples() const;

 private:
  bool configured_ = false;
  uint16_t activeAdc_ = 0;
  uint8_t consecutiveSamples_ = 0;
};
