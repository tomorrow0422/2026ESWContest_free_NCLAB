#include "pressure_configuration.h"

bool PressureConfiguration::configure(uint16_t activeAdc,
                                      uint8_t consecutiveSamples) {
  if (activeAdc == 0 || activeAdc > MAX_ACTIVE_ADC ||
      consecutiveSamples == 0) {
    return false;
  }

  activeAdc_ = activeAdc;
  consecutiveSamples_ = consecutiveSamples;
  configured_ = true;
  return true;
}

void PressureConfiguration::clear() {
  configured_ = false;
  activeAdc_ = 0;
  consecutiveSamples_ = 0;
}

bool PressureConfiguration::isConfigured() const {
  return configured_;
}

uint16_t PressureConfiguration::activeAdc() const {
  return activeAdc_;
}

uint8_t PressureConfiguration::consecutiveSamples() const {
  return consecutiveSamples_;
}
