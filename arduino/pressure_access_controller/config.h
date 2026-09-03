#pragma once

#include <Arduino.h>

// -----------------------------------------------------------------------------
// 하드웨어 핀 설정
// -----------------------------------------------------------------------------

// TLV9002 OUTA 출력은 1kΩ 보호저항을 거쳐 A0에 들어온다.
constexpr uint8_t FSR_PIN = A0;
constexpr uint8_t RED_LED_PIN = 9;
constexpr uint8_t GREEN_LED_PIN = 10;
constexpr uint8_t MOSFET_GATE_PIN = 11;

constexpr uint8_t ROW_COUNT = 4;
constexpr uint8_t COLUMN_COUNT = 3;

constexpr char keyMap[ROW_COUNT][COLUMN_COUNT] = {
    {'1', '2', '3'},
    {'4', '5', '6'},
    {'7', '8', '9'},
    {'*', '0', '#'},
};

// AK-207 커넥터를 왼쪽부터 물리 1~7번으로 봤을 때
// 1→D8, 2→D7, 3→D6, 4→D5, 5→D4, 6→D3, 7→D2로 역순 직결한다.
// AK-207 핀 기능: 1=C2, 2=R1, 3=C1, 4=R4, 5=C3, 6=R3, 7=R2
constexpr uint8_t rowPins[ROW_COUNT] = {7, 2, 3, 5};
constexpr uint8_t columnPins[COLUMN_COUNT] = {6, 8, 4};

// 기본 OLED: 1.3인치 128x64 SH1106 I2C
constexpr uint8_t OLED_I2C_ADDRESS = 0x3C;

// 현재 회로는 MOSFET Gate가 HIGH일 때 솔레노이드가 동작해 문이 열린다.
constexpr uint8_t DOOR_UNLOCK_LEVEL = HIGH;
constexpr uint8_t DOOR_LOCK_LEVEL = LOW;

// -----------------------------------------------------------------------------
// 시간, ADC, 통신 설정
// -----------------------------------------------------------------------------

constexpr uint32_t SERIAL_BAUD_RATE = 115200;
constexpr uint32_t SERIAL_CONNECT_WAIT_MS = 3000UL;
constexpr uint32_t KEYPAD_SCAN_INTERVAL_MS = 5UL;
constexpr uint32_t KEYPAD_DEBOUNCE_MS = 25UL;
constexpr uint32_t KEYPAD_LONG_PRESS_MS = 1500UL;
constexpr char KEYPAD_NO_KEY = '\0';

// UNO R4 Minima ADC의 최대 설정 해상도인 14비트를 사용한다.
// 전송 범위는 0~16383이며 별도의 2.5V 오프셋을 빼지 않는다.
constexpr uint8_t ADC_RESOLUTION_BITS = 14;
// 실제 센서와 회로를 측정한 뒤 이 값을 조정한다.
// 초록 LED는 최대 기준의 90%부터 선형으로 밝아지고 최대 기준에서 최대 밝기가 된다.
constexpr uint16_t MAX_PRESSURE_ADC = 15564;
constexpr uint16_t PRESSURE_LED_RAMP_START_ADC =
    static_cast<uint16_t>((static_cast<uint32_t>(MAX_PRESSURE_ADC) * 9U) / 10U);

constexpr uint32_t SAMPLE_INTERVAL_US = 5000UL;        // 5ms, 200Hz
constexpr uint32_t CURSOR_INTERVAL_MS = 500UL;         // ID 입력 커서 깜빡임
constexpr uint32_t MESSAGE_DURATION_MS = 2000UL;       // 안내·오류 문구 표시
constexpr uint32_t ID_INPUT_INACTIVITY_TIMEOUT_MS = 30000UL; // ID 키 입력 대기
constexpr uint32_t ID_RESPONSE_TIMEOUT_MS = 5000UL;    // ID 확인 응답 대기
constexpr uint32_t CAPTURE_RESULT_TIMEOUT_MS = 15000UL; // 캡처 결과 대기
constexpr uint32_t DOOR_UNLOCK_DURATION_MS = 20000UL;  // 문 열림 유지
constexpr uint32_t PRESSURE_INACTIVITY_TIMEOUT_MS = 3000UL;
constexpr uint32_t MAX_PATTERN_DURATION_MS = 30000UL;  // 압력 패턴 최대 입력
constexpr uint32_t CALIBRATION_PREPARE_MS = 3000UL;
constexpr uint32_t CALIBRATION_SAMPLE_COUNT = 400UL;   // 200Hz로 2초

constexpr uint8_t MAX_ID_DIGITS = 10;
constexpr uint8_t MAX_INACTIVITY_ATTEMPTS = 3;
constexpr size_t SERIAL_RX_BUFFER_SIZE = 64;
