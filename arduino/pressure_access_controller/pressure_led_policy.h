#pragma once

#include <stdint.h>

namespace PressureLedPolicy {

uint8_t brightness(uint16_t adc, uint16_t rampStartAdc,
                   uint16_t maximumPressureAdc);

}  // namespace PressureLedPolicy
