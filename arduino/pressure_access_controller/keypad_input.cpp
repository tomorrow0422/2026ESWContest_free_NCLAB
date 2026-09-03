#include "keypad_input.h"

#include <Arduino.h>

#include "config.h"

namespace {

uint32_t lastScanMs = 0;
KeypadInput::KeyEventTracker eventTracker(KEYPAD_DEBOUNCE_MS,
                                          KEYPAD_LONG_PRESS_MS,
                                          KEYPAD_NO_KEY);

char scanRaw() {
  char detectedKey = KEYPAD_NO_KEY;
  uint8_t detectedKeyCount = 0;

  // 한 번에 한 행만 LOW로 만들고 각 열이 LOW로 내려오는지 확인한다.
  for (uint8_t row = 0; row < ROW_COUNT; ++row) {
    digitalWrite(rowPins[row], LOW);
    pinMode(rowPins[row], OUTPUT);
    delayMicroseconds(5);

    for (uint8_t column = 0; column < COLUMN_COUNT; ++column) {
      if (digitalRead(columnPins[column]) == LOW) {
        detectedKey = keyMap[row][column];
        ++detectedKeyCount;
      }
    }

    // 다음 행을 검사하기 전에 현재 행을 다시 고임피던스로 돌린다.
    pinMode(rowPins[row], INPUT);
  }

  // 여러 버튼이 동시에 감지되면 잘못된 입력을 막기 위해 무시한다.
  return (detectedKeyCount == 1) ? detectedKey : KEYPAD_NO_KEY;
}

}  // namespace

namespace KeypadInput {

void begin() {
  // 행은 평상시에 고임피던스 입력으로 두고, 열은 내부 풀업을 사용한다.
  for (uint8_t row = 0; row < ROW_COUNT; ++row) {
    pinMode(rowPins[row], INPUT);
  }

  for (uint8_t column = 0; column < COLUMN_COUNT; ++column) {
    pinMode(columnPins[column], INPUT_PULLUP);
  }

  lastScanMs = 0;
  eventTracker.reset();
}

bool poll(KeyEvent& event, bool& activityStarted) {
  activityStarted = false;
  const uint32_t nowMs = millis();

  // 루프가 매우 빨라도 키패드 핀을 5ms마다 한 번만 전환한다.
  // 불필요한 고속 핀 토글과 OLED I2C 배선으로의 잡음 결합을 줄인다.
  if (nowMs - lastScanMs < KEYPAD_SCAN_INTERVAL_MS) {
    return false;
  }
  lastScanMs = nowMs;

  const char rawKey = scanRaw();
  return eventTracker.update(rawKey, nowMs, event, activityStarted);
}

}  // namespace KeypadInput
