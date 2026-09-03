#pragma once

#include <Arduino.h>

struct PressureSample {
  uint32_t elapsedUs;
  uint16_t adc;
};

namespace PressureSampler {

void begin();
void reset();
void start();
bool poll(PressureSample& sample);
uint32_t sampleCount();

}  // namespace PressureSampler
