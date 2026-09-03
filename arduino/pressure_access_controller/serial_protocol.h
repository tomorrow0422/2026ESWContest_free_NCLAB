#pragma once

#include <Arduino.h>

#include "device_types.h"

enum class PiCommandType : uint8_t {
  UNKNOWN,
  PING,
  PRESSURE_CONFIG,
  PRESSURE_MONITOR_START,
  PRESSURE_MONITOR_STOP,
  CALIBRATION_REQUIRED,
  CAPTURE_CANCEL,
  STATUS_REQUEST,
  CAPTURE_REQUEST_CALIBRATION,
  CAPTURE_REQUEST_REGISTRATION_FIRST,
  CAPTURE_REQUEST_REGISTRATION_SECOND,
  CAPTURE_RETRY_REGISTRATION_SECOND,
  CAPTURE_RESTART_REGISTRATION_FIRST,
  ID_OK,
  ID_NOT_FOUND,
  AUTH_OK,
  AUTH_FAIL,
  CAPTURE_OK_CALIBRATION,
  CAPTURE_OK_REGISTRATION,
  CAPTURE_FAIL,
  PATTERN_ERROR,
  AUTH_PROCESSING_ERROR,
};

struct PiCommand {
  PiCommandType type;
  bool validPayload;
  uint32_t value;
  uint16_t activeAdc;
  uint8_t consecutiveSamples;
};

enum class CaptureCancelReason : uint8_t {
  PI_REQUEST,
  USER,
  INACTIVITY,
};

namespace SerialProtocol {

void begin();
bool poll(PiCommand& command);

void sendBootReady();
void sendDoorLocked();
void sendDoorUnlocked();
void sendIdCheck(const char* userId);
void sendPatternStart();
void sendPatternReset();
void sendPatternEnd(uint32_t sampleCount);
void sendPressureSample(uint32_t elapsedUs, uint16_t adc);
void sendPressureMonitorSample(uint32_t elapsedUs, uint16_t adc);
void sendPressureMonitorStarted();
void sendPressureMonitorStopped();
void sendPressureMonitorRejectedBusy();
void sendPatternTimeout();
void sendPatternInactivity(uint8_t remainingAttempts);
void sendPressureConfigured(uint16_t activeAdc, uint8_t consecutiveSamples);
void sendCaptureCancelled(CaptureCancelReason reason);
void sendStatus(DeviceProtocolMode mode, bool doorUnlocked,
                bool pressureConfigured);
void sendIdTimeout();
void sendResultTimeout();
void sendPong();
void sendCaptureRejectedBusy();

}  // namespace SerialProtocol
