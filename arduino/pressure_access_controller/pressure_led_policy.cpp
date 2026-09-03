#include "pressure_led_policy.h"

namespace PressureLedPolicy {

uint8_t brightness(uint16_t adc, uint16_t rampStartAdc,
                   uint16_t maximumPressureAdc) {
  if (maximumPressureAdc <= rampStartAdc) {
    return adc >= maximumPressureAdc ? UINT8_MAX : 0;
  }
  if (adc <= rampStartAdc) {
    return 0;
  }
  if (adc >= maximumPressureAdc) {
    return UINT8_MAX;
  }

  const uint32_t progress = static_cast<uint32_t>(adc - rampStartAdc);
  const uint32_t range = maximumPressureAdc - rampStartAdc;
  return static_cast<uint8_t>((progress * UINT8_MAX) / range);
}

}  // namespace PressureLedPolicy
