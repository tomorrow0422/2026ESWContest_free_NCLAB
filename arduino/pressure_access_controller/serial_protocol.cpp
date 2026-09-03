#include "serial_protocol.h"

#include <Arduino.h>
#include <string.h>

#include "config.h"
#include "serial_line_reader.h"

namespace {

char rxBuffer[SERIAL_RX_BUFFER_SIZE] = {};
SerialLineReader rxLineReader(rxBuffer, sizeof(rxBuffer));

bool parseDecimal(const char* text, uint32_t& value) {
  if (*text == '\0') {
    return false;
  }

  value = 0;
  for (const char* cursor = text; *cursor != '\0'; ++cursor) {
    if (*cursor < '0' || *cursor > '9') {
      return false;
    }
    const uint32_t digit = static_cast<uint32_t>(*cursor - '0');
    if (value > (UINT32_MAX - digit) / 10UL) {
      return false;
    }
    value = value * 10UL + digit;
  }
  return true;
}

bool parseDecimalField(const char* begin, const char* end, uint32_t maxValue,
                       uint32_t& value) {
  if (begin == end) {
    return false;
  }

  value = 0;
  for (const char* cursor = begin; cursor != end; ++cursor) {
    if (*cursor < '0' || *cursor > '9') {
      return false;
    }
    const uint32_t digit = static_cast<uint32_t>(*cursor - '0');
    if (value > (maxValue - digit) / 10UL) {
      return false;
    }
    value = value * 10UL + digit;
  }
  return true;
}

bool parsePressureConfig(const char* payload, uint16_t& activeAdc,
                         uint8_t& consecutiveSamples) {
  const char* separator = strchr(payload, ',');
  if (separator == nullptr || strchr(separator + 1, ',') != nullptr) {
    return false;
  }

  uint32_t parsedActiveAdc = 0;
  uint32_t parsedConsecutiveSamples = 0;
  const char* payloadEnd = payload + strlen(payload);
  constexpr uint32_t maxAdc = (1UL << ADC_RESOLUTION_BITS) - 1UL;
  if (!parseDecimalField(payload, separator, maxAdc, parsedActiveAdc) ||
      parsedActiveAdc == 0 ||
      !parseDecimalField(separator + 1, payloadEnd, MAX_CONSECUTIVE_SAMPLES,
                         parsedConsecutiveSamples) ||
      parsedConsecutiveSamples == 0) {
    return false;
  }

  activeAdc = static_cast<uint16_t>(parsedActiveAdc);
  consecutiveSamples = static_cast<uint8_t>(parsedConsecutiveSamples);
  return true;
}

bool isPatternErrorReason(const char* reason) {
  if (*reason == '\0') {
    return false;
  }

  for (const char* cursor = reason; *cursor != '\0'; ++cursor) {
    if ((*cursor < 'A' || *cursor > 'Z') && *cursor != '_') {
      return false;
    }
  }
  return true;
}

PiCommand parseCommand(const char* message) {
  PiCommand command{PiCommandType::UNKNOWN, false, 0, 0, 0};

  if (strcmp(message, "PING") == 0) {
    command.type = PiCommandType::PING;
  } else if (strcmp(message, "PRESSURE_CONFIG") == 0) {
    command.type = PiCommandType::PRESSURE_CONFIG;
  } else if (strncmp(message, "PRESSURE_CONFIG,", 16) == 0) {
    command.type = PiCommandType::PRESSURE_CONFIG;
    command.validPayload =
        parsePressureConfig(message + 16, command.activeAdc,
                            command.consecutiveSamples);
  } else if (strcmp(message, "PRESSURE_MONITOR_START") == 0) {
    command.type = PiCommandType::PRESSURE_MONITOR_START;
  } else if (strcmp(message, "PRESSURE_MONITOR_STOP") == 0) {
    command.type = PiCommandType::PRESSURE_MONITOR_STOP;
  } else if (strcmp(message, "CALIBRATION_REQUIRED") == 0) {
    command.type = PiCommandType::CALIBRATION_REQUIRED;
  } else if (strcmp(message, "CAPTURE_CANCEL") == 0) {
    command.type = PiCommandType::CAPTURE_CANCEL;
  } else if (strcmp(message, "STATUS_REQUEST") == 0) {
    command.type = PiCommandType::STATUS_REQUEST;
  } else if (strcmp(message, "CAPTURE_REQUEST,CALIBRATION") == 0) {
    command.type = PiCommandType::CAPTURE_REQUEST_CALIBRATION;
  } else if (strcmp(message, "CAPTURE_REQUEST,REGISTRATION,1") == 0) {
    command.type = PiCommandType::CAPTURE_REQUEST_REGISTRATION_FIRST;
  } else if (strcmp(message, "CAPTURE_REQUEST,REGISTRATION,2") == 0) {
    command.type = PiCommandType::CAPTURE_REQUEST_REGISTRATION_SECOND;
  } else if (strncmp(message, "CAPTURE_RETRY,REGISTRATION,2,", 29) == 0) {
    command.type = PiCommandType::CAPTURE_RETRY_REGISTRATION_SECOND;
    const char* remaining = message + 29;
    command.validPayload =
        strcmp(remaining, "1") == 0 || strcmp(remaining, "2") == 0;
  } else if (strcmp(message, "CAPTURE_RESTART,REGISTRATION,1") == 0) {
    command.type = PiCommandType::CAPTURE_RESTART_REGISTRATION_FIRST;
  } else if (strcmp(message, "ID_OK") == 0) {
    command.type = PiCommandType::ID_OK;
  } else if (strcmp(message, "ID_NOT_FOUND") == 0) {
    command.type = PiCommandType::ID_NOT_FOUND;
  } else if (strcmp(message, "AUTH_OK") == 0) {
    command.type = PiCommandType::AUTH_OK;
  } else if (strncmp(message, "AUTH_FAIL,", 10) == 0) {
    command.type = PiCommandType::AUTH_FAIL;
    command.validPayload = parseDecimal(message + 10, command.value);
  } else if (strcmp(message, "CAPTURE_OK,CALIBRATION") == 0) {
    command.type = PiCommandType::CAPTURE_OK_CALIBRATION;
  } else if (strcmp(message, "CAPTURE_OK,REGISTRATION") == 0) {
    command.type = PiCommandType::CAPTURE_OK_REGISTRATION;
  } else if (strncmp(message, "CAPTURE_FAIL,", 13) == 0) {
    command.type = PiCommandType::CAPTURE_FAIL;
  } else if (strncmp(message, "PATTERN_ERROR,", 14) == 0) {
    command.type = PiCommandType::PATTERN_ERROR;
    command.validPayload = isPatternErrorReason(message + 14);
  } else if (strcmp(message, "ERROR,AUTH_PROCESSING") == 0) {
    command.type = PiCommandType::AUTH_PROCESSING_ERROR;
  }

  return command;
}

void sendRxOverflow() {
  Serial.println(F("ERROR,RX_OVERFLOW"));
}

}  // namespace

namespace SerialProtocol {

void begin() {
  Serial.begin(SERIAL_BAUD_RATE);

  // USB 시리얼이 리셋 후 다시 연결될 시간을 제한적으로 기다린다.
  const uint32_t startedMs = millis();
  while (!Serial && millis() - startedMs < SERIAL_CONNECT_WAIT_MS) {
  }
}

bool poll(PiCommand& command) {
  while (Serial.available() > 0) {
    const char incoming = static_cast<char>(Serial.read());
    const SerialLineReadResult result = rxLineReader.push(incoming);

    if (result == SerialLineReadResult::COMPLETE) {
      command = parseCommand(rxLineReader.line());
      return true;
    }
    if (result == SerialLineReadResult::OVERFLOW) {
      sendRxOverflow();
    }
  }

  return false;
}

void sendBootReady() {
  Serial.println(F("BOOT,READY"));
}

void sendDoorLocked() {
  Serial.println(F("DOOR,LOCKED"));
}

void sendDoorUnlocked() {
  Serial.println(F("DOOR,UNLOCKED"));
}

void sendIdCheck(const char* userId) {
  Serial.print(F("ID_CHECK,"));
  Serial.println(userId);
}

void sendPatternStart() {
  Serial.println(F("PATTERN_START"));
}

void sendPatternReset() {
  Serial.println(F("PATTERN_RESET"));
}

void sendPatternEnd(uint32_t sampleCount) {
  Serial.print(F("PATTERN_END,"));
  Serial.println(sampleCount);
}

void sendPressureSample(uint32_t elapsedUs, uint16_t adc) {
  Serial.print(F("P,"));
  Serial.print(elapsedUs);
  Serial.print(',');
  Serial.println(adc);
}

void sendPressureMonitorSample(uint32_t elapsedUs, uint16_t adc) {
  Serial.print(F("M,"));
  Serial.print(elapsedUs);
  Serial.print(',');
  Serial.println(adc);
}

void sendPressureMonitorStarted() {
  Serial.println(F("PRESSURE_MONITOR_STARTED"));
}

void sendPressureMonitorStopped() {
  Serial.println(F("PRESSURE_MONITOR_STOPPED"));
}

void sendPressureMonitorRejectedBusy() {
  Serial.println(F("PRESSURE_MONITOR_REJECTED,BUSY"));
}

void sendPatternTimeout() {
  Serial.println(F("PATTERN_ABORT,TIMEOUT"));
}

void sendPatternInactivity(uint8_t remainingAttempts) {
  if (remainingAttempts < 1 || remainingAttempts > 2) {
    return;
  }
  Serial.print(F("PATTERN_ABORT,INACTIVITY,"));
  Serial.println(remainingAttempts);
}

void sendPressureConfigured(uint16_t activeAdc, uint8_t consecutiveSamples) {
  Serial.print(F("PRESSURE_CONFIGURED,"));
  Serial.print(activeAdc);
  Serial.print(',');
  Serial.println(consecutiveSamples);
}

void sendCaptureCancelled(CaptureCancelReason reason) {
  switch (reason) {
    case CaptureCancelReason::PI_REQUEST:
      Serial.println(F("CAPTURE_CANCELLED,PI"));
      break;
    case CaptureCancelReason::USER:
      Serial.println(F("CAPTURE_CANCELLED,USER"));
      break;
    case CaptureCancelReason::INACTIVITY:
      Serial.println(F("CAPTURE_CANCELLED,INACTIVITY"));
      break;
  }
}

void sendStatus(DeviceProtocolMode mode, bool doorUnlocked,
                bool pressureConfigured) {
  Serial.print(F("STATUS,"));
  switch (mode) {
    case DeviceProtocolMode::IDLE:
      Serial.print(F("IDLE"));
      break;
    case DeviceProtocolMode::AUTHENTICATION:
      Serial.print(F("AUTHENTICATION"));
      break;
    case DeviceProtocolMode::REGISTRATION_FIRST:
      Serial.print(F("REGISTRATION_FIRST"));
      break;
    case DeviceProtocolMode::REGISTRATION_SECOND:
      Serial.print(F("REGISTRATION_SECOND"));
      break;
    case DeviceProtocolMode::CALIBRATION:
      Serial.print(F("CALIBRATION"));
      break;
    case DeviceProtocolMode::PRESSURE_MONITOR:
      Serial.print(F("PRESSURE_MONITOR"));
      break;
    case DeviceProtocolMode::AUTH_SUCCESS:
      Serial.print(F("AUTH_SUCCESS"));
      break;
  }
  Serial.print(doorUnlocked ? F(",UNLOCKED,") : F(",LOCKED,"));
  Serial.println(pressureConfigured ? F("CONFIGURED") : F("REQUIRED"));
}

void sendIdTimeout() {
  Serial.println(F("TIMEOUT,ID"));
}

void sendResultTimeout() {
  Serial.println(F("TIMEOUT,RESULT"));
}

void sendPong() {
  Serial.println(F("PONG"));
}

void sendCaptureRejectedBusy() {
  Serial.println(F("CAPTURE_REJECTED,BUSY"));
}

}  // namespace SerialProtocol
