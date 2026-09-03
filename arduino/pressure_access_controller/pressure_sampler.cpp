#include "pressure_sampler.h"

#include <Arduino.h>

#include "config.h"

namespace {

uint32_t nextSampleUs = 0;
uint32_t captureStartedUs = 0;
uint32_t capturedSampleCount = 0;

}  // namespace

namespace PressureSampler {

void begin() {
  analogReadResolution(ADC_RESOLUTION_BITS);
}

void reset() {
  nextSampleUs = 0;
  captureStartedUs = 0;
  capturedSampleCount = 0;
}

void start() {
  capturedSampleCount = 0;
  captureStartedUs = micros();
  nextSampleUs = captureStartedUs + SAMPLE_INTERVAL_US;
}

bool poll(PressureSample& sample) {
  const uint32_t nowUs = micros();
  const int32_t latenessUs = static_cast<int32_t>(nowUs - nextSampleUs);

  if (latenessUs < 0) {
    return false;
  }

  // 루프가 5ms 이상 늦어졌을 때 과거 샘플을 모아서 측정하지 않고
  // 늦어진 측정 슬롯을 건너뛰어 실제 시간축을 유지한다.
  if (static_cast<uint32_t>(latenessUs) >= SAMPLE_INTERVAL_US) {
    const uint32_t skippedSlots =
        static_cast<uint32_t>(latenessUs) / SAMPLE_INTERVAL_US;
    nextSampleUs += skippedSlots * SAMPLE_INTERVAL_US;
  }

  sample.elapsedUs = nowUs - captureStartedUs;
  sample.adc = static_cast<uint16_t>(analogRead(FSR_PIN));
  ++capturedSampleCount;
  nextSampleUs += SAMPLE_INTERVAL_US;
  return true;
}

uint32_t sampleCount() {
  return capturedSampleCount;
}

}  // namespace PressureSampler
