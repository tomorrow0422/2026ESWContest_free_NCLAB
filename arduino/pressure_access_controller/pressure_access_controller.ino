#include <Arduino.h>

#include "capture_cancel_policy.h"
#include "capture_auth_policy.h"
#include "config.h"
#include "controller_policy.h"
#include "device_types.h"
#include "device_session_policy.h"
#include "display_ui.h"
#include "id_input_policy.h"
#include "inactivity_retry_counter.h"
#include "keypad_input.h"
#include "pressure_activity_tracker.h"
#include "pressure_configuration.h"
#include "pressure_led_policy.h"
#include "pressure_sampler.h"
#include "serial_protocol.h"

/*
 * 압력 기반 출입 제어기
 *
 * 역할
 *  - 키패드에서 User ID를 입력받아 Raspberry Pi에 등록 여부를 요청한다.
 *  - TLV9002IDR을 거친 FSR402 신호를 A0에서 200Hz로 읽어 실시간 전송한다.
 *  - Raspberry Pi의 인증 결과에 따라 LED, MOSFET, 솔레노이드 잠금장치를 제어한다.
 *
 * 키 동작
 *  - 대기 상태의 아무 키: OLED를 켜고 ID 입력 화면으로 이동
 *  - ID 입력 중 숫자: ID 추가
 *  - ID 입력 중 짧은 #: 마지막 한 자리 삭제
 *  - ID 입력 중 긴 #: 전체 삭제, 빈 ID면 대기 상태로 복귀
 *  - ID 입력 중 *: 등록 여부 확인 요청
 *  - 압력 입력 중 짧은 #: 현재 압력 패턴을 처음부터 다시 수집
 *  - 인증·등록 중 긴 #: 현재 작업 취소
 *  - 압력 입력 중 *: 압력 수집 종료 및 인증 요청
 *  - 인증 성공 중 #: 즉시 문 잠금
 *
 * 통신
 *  - Arduino -> Pi: ID_CHECK, PATTERN_START, P, PATTERN_RESET, PATTERN_END,
 *                   PRESSURE_MONITOR_STARTED, M, PRESSURE_MONITOR_STOPPED
 *  - Pi -> Arduino: ID_OK, ID_NOT_FOUND, AUTH_OK, AUTH_FAIL,
 *                   CAPTURE_REQUEST, CAPTURE_RETRY, CAPTURE_RESTART,
 *                   CAPTURE_OK, CAPTURE_FAIL, PATTERN_ERROR, ERROR,
 *                   PRESSURE_MONITOR_START, PRESSURE_MONITOR_STOP
 */

DeviceState state = DeviceState::STANDBY;
DeviceState stateAfterMessage = DeviceState::ID_INPUT;
CaptureMode captureMode = CaptureMode::NONE;
PressureConfiguration pressureConfiguration;
PressureActivityTracker pressureActivityTracker;
InactivityRetryCounter inactivityRetryCounter(MAX_INACTIVITY_ATTEMPTS);

uint32_t stateStartedMs = 0;
uint32_t messageDurationMs = MESSAGE_DURATION_MS;
uint32_t nextCursorToggleMs = 0;
uint32_t lastIdActivityMs = 0;
uint32_t patternStartedMs = 0;

// 실제 ID는 배열에 그대로 보관하고, OLED에서만 앞자리들을 '*'로 가린다.
char userId[MAX_ID_DIGITS + 1] = {};
uint8_t userIdLength = 0;

bool cursorVisible = true;
bool doorUnlocked = false;
bool keyGestureActive = false;
DeviceState keyGestureStartedState = DeviceState::STANDBY;

// -----------------------------------------------------------------------------
// 함수 원형 선언
// -----------------------------------------------------------------------------

void enterStandby();
void enterIdInput();
void enterRegistrationReady(CaptureMode mode);
void enterCalibrationReady();
void enterPatternInput();
void enterPressureMonitor();
void enterCaptureResultWait();
void enterAuthenticationSuccess();
void enterTimedMessage(MessageKind message, DeviceState nextState,
                       uint32_t durationMs = MESSAGE_DURATION_MS);

void initializeDoorHardware();
void processKeypadInput();
void handleKeypadCommand(const KeypadInput::KeyEvent& event);
void processSerialInput();
void handlePiCommand(const PiCommand& command);
void updateStateTimers();
void updateCursor();
void updatePressureCapture();
void handlePressureInactivityTimeout();
void cancelCaptureByUser();
void cancelCaptureFromPi();
void stopPressureMonitor();
void sendCurrentStatus();

// -----------------------------------------------------------------------------
// 초기 설정과 메인 루프
// -----------------------------------------------------------------------------

void setup() {
  initializeDoorHardware();

  SerialProtocol::begin();

  PressureSampler::begin();
  KeypadInput::begin();

  DisplayUi::begin();

  state = DeviceState::STANDBY;
  stateStartedMs = millis();
  SerialProtocol::sendBootReady();
}

void loop() {
  processSerialInput();
  processKeypadInput();
  updatePressureCapture();
  updateCursor();
  updateStateTimers();
}

void initializeDoorHardware() {
  // 부팅 중 출력이 흔들려 문이 열리지 않도록 가장 먼저 잠금 상태를 만든다.
  pinMode(RED_LED_PIN, OUTPUT);
  pinMode(GREEN_LED_PIN, OUTPUT);
  pinMode(MOSFET_GATE_PIN, OUTPUT);
  digitalWrite(MOSFET_GATE_PIN, DOOR_LOCK_LEVEL);
  digitalWrite(RED_LED_PIN, HIGH);
  analogWrite(GREEN_LED_PIN, 0);
}

void processKeypadInput() {
  KeypadInput::KeyEvent event;
  bool activityStarted = false;
  const bool hasEvent = KeypadInput::poll(event, activityStarted);

  if (activityStarted && state == DeviceState::ID_INPUT) {
    lastIdActivityMs = millis();
  }
  if (activityStarted) {
    keyGestureStartedState = state;
    keyGestureActive = true;
  }

  if (!hasEvent) {
    return;
  }

  if (keyGestureActive && keyGestureStartedState != state) {
    keyGestureActive = false;
    return;
  }
  keyGestureActive = false;

  handleKeypadCommand(event);
}

// -----------------------------------------------------------------------------
// 상태 전환과 출력 제어
// -----------------------------------------------------------------------------

void clearUserId() {
  memset(userId, 0, sizeof(userId));
  userIdLength = 0;
}

void lockDoor() {
  // 잠긴 상태: MOSFET OFF, 초록 LED OFF, 빨간 LED ON
  digitalWrite(MOSFET_GATE_PIN, DOOR_LOCK_LEVEL);
  analogWrite(GREEN_LED_PIN, 0);
  digitalWrite(RED_LED_PIN, HIGH);
  doorUnlocked = false;
}

void unlockDoor() {
  // 인증 성공 상태: 빨간 LED OFF, 초록 LED ON, MOSFET ON
  digitalWrite(RED_LED_PIN, LOW);
  analogWrite(GREEN_LED_PIN, UINT8_MAX);
  digitalWrite(MOSFET_GATE_PIN, DOOR_UNLOCK_LEVEL);
  doorUnlocked = true;
}

void lockDoorBeforeStateTransition() {
  const bool shouldReport = doorUnlocked;
  lockDoor();
  if (shouldReport) {
    SerialProtocol::sendDoorLocked();
  }
}

void enterStandby() {
  // 한 번의 인증 절차가 끝났으므로 사용자별 임시 상태를 모두 비운다.
  lockDoorBeforeStateTransition();
  clearUserId();
  PressureSampler::reset();
  pressureActivityTracker.stop();
  inactivityRetryCounter.reset();
  captureMode = CaptureMode::NONE;
  keyGestureActive = false;

  state = DeviceState::STANDBY;
  stateStartedMs = millis();

  DisplayUi::sleep();
}

void enterIdInput() {
  lockDoorBeforeStateTransition();
  clearUserId();
  const uint32_t nowMs = millis();
  cursorVisible = true;
  nextCursorToggleMs = nowMs + CURSOR_INTERVAL_MS;
  captureMode = CaptureMode::NONE;

  state = DeviceState::ID_INPUT;
  stateStartedMs = nowMs;
  lastIdActivityMs = nowMs;
  DisplayUi::showIdInput(userId, userIdLength, cursorVisible);
}

void requestIdCheck() {
  if (!pressureConfiguration.isConfigured()) {
    enterTimedMessage(MessageKind::CALIBRATION_REQUIRED,
                      DeviceState::STANDBY);
    return;
  }

  SerialProtocol::sendIdCheck(userId);

  state = DeviceState::WAIT_ID_RESULT;
  stateStartedMs = millis();
  DisplayUi::showIdWait();
}

void enterRegistrationReady(CaptureMode mode) {
  lockDoorBeforeStateTransition();
  captureMode = mode;
  state = DeviceState::REGISTRATION_READY;
  stateStartedMs = millis();
  DisplayUi::showRegistrationReady(captureMode);
}

void enterCalibrationReady() {
  lockDoorBeforeStateTransition();
  inactivityRetryCounter.reset();
  captureMode = CaptureMode::CALIBRATION;
  state = DeviceState::CALIBRATION_READY;
  stateStartedMs = millis();
  DisplayUi::showCalibrationReady();
}

void enterPatternInput() {
  if (captureMode != CaptureMode::CALIBRATION &&
      !pressureConfiguration.isConfigured()) {
    enterTimedMessage(MessageKind::CALIBRATION_REQUIRED,
                      DeviceState::STANDBY);
    return;
  }

  lockDoorBeforeStateTransition();
  state = DeviceState::PATTERN_INPUT;
  stateStartedMs = millis();

  DisplayUi::showPatternInput(captureMode);

  SerialProtocol::sendPatternStart();

  // OLED 전송과 PATTERN_START 출력이 끝난 뒤 첫 5ms 마감시간을 설정한다.
  PressureSampler::start();
  patternStartedMs = millis();
  if (captureMode != CaptureMode::CALIBRATION) {
    if (!pressureActivityTracker.configure(
            pressureConfiguration.activeAdc(),
            pressureConfiguration.consecutiveSamples())) {
      enterTimedMessage(MessageKind::CALIBRATION_REQUIRED,
                        DeviceState::STANDBY);
      return;
    }
    pressureActivityTracker.start(patternStartedMs);
  }
}

void enterPressureMonitor() {
  lockDoorBeforeStateTransition();
  clearUserId();
  pressureActivityTracker.stop();
  inactivityRetryCounter.reset();
  captureMode = CaptureMode::NONE;
  state = DeviceState::PRESSURE_MONITOR;
  stateStartedMs = millis();

  // 사용자에게 압력값이나 패턴 안내를 표시하지 않는다.
  DisplayUi::sleep();
  SerialProtocol::sendPressureMonitorStarted();
  PressureSampler::start();
}

void resetPressurePattern() {
  SerialProtocol::sendPatternReset();
  PressureSampler::start();
  patternStartedMs = millis();
  pressureActivityTracker.start(patternStartedMs);
}

void enterCaptureResultWait() {
  lockDoorBeforeStateTransition();
  SerialProtocol::sendPatternEnd(PressureSampler::sampleCount());

  if (captureMode != CaptureMode::CALIBRATION &&
      pressureActivityTracker.hasDetectedActivity()) {
    inactivityRetryCounter.reset();
  }

  state = DeviceState::WAIT_CAPTURE_RESULT;
  stateStartedMs = millis();
  DisplayUi::showCaptureResultWait(captureMode);
}

void enterAuthenticationSuccess() {
  state = DeviceState::AUTH_SUCCESS;
  stateStartedMs = millis();
  unlockDoor();
  DisplayUi::showAuthenticationSuccess();
  SerialProtocol::sendDoorUnlocked();
}

void enterTimedMessage(MessageKind message, DeviceState nextState,
                       uint32_t durationMs) {
  lockDoorBeforeStateTransition();
  // 메시지를 표시한 뒤 nextState로 자동 이동하기 위한 공통 처리다.
  stateAfterMessage = nextState;
  messageDurationMs = durationMs;
  state = DeviceState::TIMED_MESSAGE;
  stateStartedMs = millis();
  DisplayUi::showMessage(message);
}

// -----------------------------------------------------------------------------
// 키패드 입력 처리
// -----------------------------------------------------------------------------

void handleKeypadCommand(const KeypadInput::KeyEvent& event) {
  const char key = event.key;

  if (state == DeviceState::STANDBY) {
    // 첫 키는 화면을 깨우는 용도이며 User ID 첫 숫자로 저장하지 않는다.
    if (!pressureConfiguration.isConfigured()) {
      enterTimedMessage(MessageKind::CALIBRATION_REQUIRED,
                        DeviceState::STANDBY);
      return;
    }
    enterIdInput();
    return;
  }

  if (state == DeviceState::ID_INPUT) {
    // 숫자 입력: 최대 10자리까지 ID 배열 뒤에 추가한다.
    if (key >= '0' && key <= '9') {
      if (userIdLength < MAX_ID_DIGITS) {
        userId[userIdLength++] = key;
        userId[userIdLength] = '\0';
        cursorVisible = true;
        nextCursorToggleMs = millis() + CURSOR_INTERVAL_MS;
        DisplayUi::showIdInput(userId, userIdLength, cursorVisible);
      }
      return;
    }

    if (key == '#') {
      const IdInputPolicy::HashAction action =
          IdInputPolicy::hashAction(event.type, userIdLength);
      if (action == IdInputPolicy::HashAction::ENTER_STANDBY) {
        enterStandby();
        return;
      }
      if (action == IdInputPolicy::HashAction::DELETE_LAST) {
        --userIdLength;
        userId[userIdLength] = '\0';
      } else if (action == IdInputPolicy::HashAction::CLEAR_ALL) {
        clearUserId();
      } else {
        return;
      }
      cursorVisible = true;
      nextCursorToggleMs = millis() + CURSOR_INTERVAL_MS;
      DisplayUi::showIdInput(userId, userIdLength, cursorVisible);
      return;
    }

    if (key == '*' && userIdLength > 0) {
      // ID가 한 자리 이상 있을 때만 등록 여부를 확인한다.
      requestIdCheck();
      return;
    }
  }

  if (state == DeviceState::REGISTRATION_READY) {
    if (key == '#' &&
        event.type == KeypadInput::KeyEventType::LONG_PRESS) {
      cancelCaptureByUser();
      return;
    }
    if (key == '*') {
      enterPatternInput();
      return;
    }
  }

  if (state == DeviceState::PATTERN_INPUT) {
    if (captureMode != CaptureMode::CALIBRATION && key == '#' &&
        event.type == KeypadInput::KeyEventType::LONG_PRESS) {
      cancelCaptureByUser();
      return;
    }

    if (captureMode != CaptureMode::CALIBRATION && key == '#' &&
        event.type == KeypadInput::KeyEventType::SHORT_PRESS) {
      // Pi에 초기화 명령을 보내고 Arduino 샘플 번호도 0부터 다시 시작한다.
      resetPressurePattern();
      return;
    }

    if (captureMode != CaptureMode::CALIBRATION && key == '*') {
      // 압력 전송을 멈추고 Pi의 캡처 결과를 기다린다.
      enterCaptureResultWait();
      return;
    }
  }

  if (state == DeviceState::AUTH_SUCCESS && key == '#') {
    enterStandby();
    return;
  }
}

// -----------------------------------------------------------------------------
// 200Hz 압력 측정 및 실시간 전송
// -----------------------------------------------------------------------------

void updatePressureCapture() {
  if (state != DeviceState::PATTERN_INPUT &&
      state != DeviceState::PRESSURE_MONITOR) {
    return;
  }

  PressureSample sample;
  if (!PressureSampler::poll(sample)) {
    return;
  }

  if (state == DeviceState::PRESSURE_MONITOR) {
    analogWrite(GREEN_LED_PIN,
                PressureLedPolicy::brightness(
                    sample.adc, PRESSURE_LED_RAMP_START_ADC,
                    MAX_PRESSURE_ADC));
    SerialProtocol::sendPressureMonitorSample(sample.elapsedUs, sample.adc);
    return;
  }

  if (captureMode != CaptureMode::CALIBRATION) {
    pressureActivityTracker.observe(sample.adc, millis());
    analogWrite(GREEN_LED_PIN,
                PressureLedPolicy::brightness(
                    sample.adc, PRESSURE_LED_RAMP_START_ADC,
                    MAX_PRESSURE_ADC));
  }

  SerialProtocol::sendPressureSample(sample.elapsedUs, sample.adc);

  if (captureMode == CaptureMode::CALIBRATION &&
      PressureSampler::sampleCount() >= CALIBRATION_SAMPLE_COUNT) {
    enterCaptureResultWait();
  }
}

// -----------------------------------------------------------------------------
// Raspberry Pi 시리얼 수신 및 명령 처리
// -----------------------------------------------------------------------------

void processSerialInput() {
  PiCommand command;
  while (SerialProtocol::poll(command)) {
    handlePiCommand(command);
  }
}

void handlePressureInactivityTimeout() {
  PressureSampler::reset();
  const InactivityAttemptResult result =
      inactivityRetryCounter.recordTimeout();

  if (!result.exhausted) {
    SerialProtocol::sendPatternInactivity(result.remainingAttempts);
    enterTimedMessage(CaptureAuthPolicy::patternNotEnteredMessage(captureMode),
                      CaptureAuthPolicy::retryState(captureMode));
    return;
  }

  const DeviceState nextState =
      CaptureAuthPolicy::inactivityExhaustedState(captureMode);
  SerialProtocol::sendCaptureCancelled(CaptureCancelReason::INACTIVITY);
  inactivityRetryCounter.reset();
  enterTimedMessage(MessageKind::PATTERN_INPUT_CANCELLED, nextState);
}

void cancelCaptureByUser() {
  const CaptureCancelPolicy::Destination destination =
      CaptureCancelPolicy::userDestination(captureMode);
  if (destination == CaptureCancelPolicy::Destination::NONE) {
    return;
  }

  PressureSampler::reset();
  pressureActivityTracker.stop();
  inactivityRetryCounter.reset();
  SerialProtocol::sendCaptureCancelled(CaptureCancelReason::USER);

  const DeviceState nextState =
      destination == CaptureCancelPolicy::Destination::ID_INPUT
          ? DeviceState::ID_INPUT
          : DeviceState::STANDBY;
  enterTimedMessage(MessageKind::OPERATION_CANCELLED, nextState);
}

void cancelCaptureFromPi() {
  if (CaptureCancelPolicy::piDestination(captureMode) ==
      CaptureCancelPolicy::Destination::STANDBY) {
    enterStandby();
    SerialProtocol::sendCaptureCancelled(CaptureCancelReason::PI_REQUEST);
  }
}

void sendCurrentStatus() {
  const DeviceProtocolMode mode = DeviceSessionPolicy::protocolMode(
      state, captureMode, stateAfterMessage);
  SerialProtocol::sendStatus(mode, doorUnlocked,
                             pressureConfiguration.isConfigured());
}

void stopPressureMonitor() {
  if (state == DeviceState::PRESSURE_MONITOR) {
    enterStandby();
  }
  SerialProtocol::sendPressureMonitorStopped();
}

void handlePiCommand(const PiCommand& command) {
  // 연결 상태 확인용 명령은 어느 상태에서든 응답한다.
  if (command.type == PiCommandType::PING) {
    SerialProtocol::sendPong();
    return;
  }

  if (command.type == PiCommandType::STATUS_REQUEST) {
    sendCurrentStatus();
    return;
  }

  if (command.type == PiCommandType::PRESSURE_MONITOR_STOP) {
    stopPressureMonitor();
    return;
  }

  if (command.type == PiCommandType::PRESSURE_MONITOR_START) {
    if (ControllerPolicy::canStartPressureMonitor(state)) {
      enterPressureMonitor();
    } else {
      SerialProtocol::sendPressureMonitorRejectedBusy();
    }
    return;
  }

  if (command.type == PiCommandType::PRESSURE_CONFIG) {
    if (!command.validPayload) {
      return;
    }
    if (!ControllerPolicy::canApplyPressureConfiguration(state, captureMode)) {
      SerialProtocol::sendCaptureRejectedBusy();
      return;
    }
    if (pressureConfiguration.configure(command.activeAdc,
                                        command.consecutiveSamples)) {
      SerialProtocol::sendPressureConfigured(
          pressureConfiguration.activeAdc(),
          pressureConfiguration.consecutiveSamples());
    }
    return;
  }

  if (command.type == PiCommandType::CALIBRATION_REQUIRED) {
    pressureConfiguration.clear();
    if (state != DeviceState::PRESSURE_MONITOR) {
      enterStandby();
    }
    return;
  }

  if (command.type == PiCommandType::CAPTURE_CANCEL) {
    cancelCaptureFromPi();
    return;
  }

  if (command.type == PiCommandType::CAPTURE_REQUEST_CALIBRATION) {
    if (ControllerPolicy::canStartCalibration(state)) {
      enterCalibrationReady();
    } else {
      SerialProtocol::sendCaptureRejectedBusy();
    }
    return;
  }

  if (command.type == PiCommandType::CAPTURE_REQUEST_REGISTRATION_FIRST) {
    if (ControllerPolicy::canStartRegistrationFirst(
            state, pressureConfiguration.isConfigured())) {
      inactivityRetryCounter.reset();
      enterRegistrationReady(CaptureMode::REGISTRATION_FIRST);
    } else {
      SerialProtocol::sendCaptureRejectedBusy();
    }
    return;
  }

  if (command.type == PiCommandType::CAPTURE_REQUEST_REGISTRATION_SECOND) {
    if (!pressureConfiguration.isConfigured()) {
      enterStandby();
      SerialProtocol::sendCaptureRejectedBusy();
      return;
    }
    if (ControllerPolicy::canStartRegistrationSecond(
            state, pressureConfiguration.isConfigured())) {
      inactivityRetryCounter.reset();
      enterRegistrationReady(CaptureMode::REGISTRATION_SECOND);
    } else {
      SerialProtocol::sendCaptureRejectedBusy();
    }
    return;
  }

  if (command.type == PiCommandType::CAPTURE_RETRY_REGISTRATION_SECOND) {
    if (!pressureConfiguration.isConfigured()) {
      enterStandby();
      SerialProtocol::sendCaptureRejectedBusy();
      return;
    }
    if (state == DeviceState::WAIT_CAPTURE_RESULT && command.validPayload) {
      captureMode = CaptureMode::REGISTRATION_SECOND;
      enterTimedMessage(MessageKind::REGISTRATION_RETRY,
                        DeviceState::REGISTRATION_READY);
    } else {
      SerialProtocol::sendCaptureRejectedBusy();
    }
    return;
  }

  if (command.type == PiCommandType::CAPTURE_RESTART_REGISTRATION_FIRST) {
    if (!pressureConfiguration.isConfigured()) {
      enterStandby();
      SerialProtocol::sendCaptureRejectedBusy();
      return;
    }
    if (state == DeviceState::WAIT_CAPTURE_RESULT) {
      captureMode = CaptureMode::REGISTRATION_FIRST;
      inactivityRetryCounter.reset();
      enterTimedMessage(MessageKind::REGISTRATION_RESTART,
                        DeviceState::REGISTRATION_READY);
    } else {
      SerialProtocol::sendCaptureRejectedBusy();
    }
    return;
  }

  if (state == DeviceState::WAIT_ID_RESULT) {
    // 등록된 ID면 압력 입력을 시작한다.
    if (command.type == PiCommandType::ID_OK) {
      if (!pressureConfiguration.isConfigured()) {
        enterTimedMessage(MessageKind::CALIBRATION_REQUIRED,
                          DeviceState::STANDBY);
        return;
      }
      inactivityRetryCounter.reset();
      captureMode = CaptureMode::AUTHENTICATION;
      enterPatternInput();
      return;
    }

    if (command.type == PiCommandType::ID_NOT_FOUND) {
      enterTimedMessage(MessageKind::UNREGISTERED_USER,
                        DeviceState::ID_INPUT);
      return;
    }
  }

  if (state == DeviceState::WAIT_CAPTURE_RESULT) {
    if (command.type == PiCommandType::AUTH_OK) {
      if (captureMode == CaptureMode::AUTHENTICATION) {
        enterAuthenticationSuccess();
      } else {
        // 등록이나 캘리브레이션 결과 대기 중 도착한 인증 성공 응답은
        // 잠금 해제에 사용하지 않고 현재 수집 단계부터 다시 시도한다.
        enterTimedMessage(MessageKind::PI_ERROR,
                          CaptureAuthPolicy::retryState(captureMode));
      }
      return;
    }

    if (command.type == PiCommandType::CAPTURE_OK_CALIBRATION &&
        captureMode == CaptureMode::CALIBRATION) {
      enterTimedMessage(pressureConfiguration.isConfigured()
                            ? MessageKind::CALIBRATION_SUCCESS
                            : MessageKind::CALIBRATION_REQUIRED,
                        DeviceState::STANDBY);
      return;
    }

    if (command.type == PiCommandType::CAPTURE_OK_REGISTRATION &&
        CaptureAuthPolicy::isRegistration(captureMode)) {
      enterTimedMessage(MessageKind::REGISTRATION_SUCCESS,
                        DeviceState::STANDBY);
      return;
    }

    if (command.type == PiCommandType::CAPTURE_FAIL) {
      enterTimedMessage(MessageKind::CAPTURE_FAILED,
                        DeviceState::STANDBY);
      return;
    }

    if (command.type == PiCommandType::AUTH_FAIL) {
      const CaptureAuthPolicy::Transition transition =
          CaptureAuthPolicy::authenticationFailure(command.validPayload,
                                                   command.value);
      enterTimedMessage(transition.message, transition.nextState);
      return;
    }

    if (command.type == PiCommandType::PATTERN_ERROR) {
      enterTimedMessage(command.validPayload ? MessageKind::PATTERN_DATA_ERROR
                                             : MessageKind::PI_ERROR,
                        CaptureAuthPolicy::retryState(captureMode));
      return;
    }
  }

  if (command.type == PiCommandType::AUTH_PROCESSING_ERROR) {
    const DeviceState nextState =
        CaptureAuthPolicy::authenticationProcessingErrorState(state);
    enterTimedMessage(MessageKind::PI_ERROR, nextState);
  }
}

// -----------------------------------------------------------------------------
// millis() 기반 커서·통신·문 잠금 타이머
// -----------------------------------------------------------------------------

void updateCursor() {
  if (state != DeviceState::ID_INPUT) {
    return;
  }

  const uint32_t nowMs = millis();
  if (static_cast<int32_t>(nowMs - nextCursorToggleMs) >= 0) {
    cursorVisible = !cursorVisible;
    nextCursorToggleMs += CURSOR_INTERVAL_MS;
    DisplayUi::showIdInput(userId, userIdLength, cursorVisible);
  }
}

void updateStateTimers() {
  const uint32_t nowMs = millis();
  const uint32_t elapsedMs = nowMs - stateStartedMs;

  if (state == DeviceState::ID_INPUT &&
      IdInputPolicy::hasTimedOut(nowMs, lastIdActivityMs,
                                 ID_INPUT_INACTIVITY_TIMEOUT_MS)) {
    enterStandby();
    return;
  }

  if (state == DeviceState::CALIBRATION_READY &&
      elapsedMs >= CALIBRATION_PREPARE_MS) {
    enterPatternInput();
    return;
  }

  if (state == DeviceState::PATTERN_INPUT &&
      captureMode != CaptureMode::CALIBRATION &&
      pressureActivityTracker.hasTimedOut(
          nowMs, PRESSURE_INACTIVITY_TIMEOUT_MS)) {
    handlePressureInactivityTimeout();
    return;
  }

  if (state == DeviceState::PATTERN_INPUT &&
      nowMs - patternStartedMs >= MAX_PATTERN_DURATION_MS) {
    // 사용자가 종료 키를 누르지 않으면 현재 패턴을 인증하지
    // 않고 폐기한 뒤, 같은 ID의 압력 패턴 입력을 다시 시작한다.
    inactivityRetryCounter.reset();
    SerialProtocol::sendPatternTimeout();
    enterTimedMessage(MessageKind::PATTERN_TIMEOUT,
                      CaptureAuthPolicy::retryState(captureMode));
    return;
  }

  if (state == DeviceState::WAIT_ID_RESULT &&
      elapsedMs >= ID_RESPONSE_TIMEOUT_MS) {
    // ID 확인 응답이 5초 안에 오지 않으면 ID 입력부터 다시 시작한다.
    SerialProtocol::sendIdTimeout();
    enterTimedMessage(MessageKind::COMMUNICATION_ERROR,
                      DeviceState::ID_INPUT);
    return;
  }

  if (state == DeviceState::WAIT_CAPTURE_RESULT &&
      elapsedMs >= CAPTURE_RESULT_TIMEOUT_MS) {
    // 캡처 결과가 15초 안에 오지 않으면 현재 캡처 단계를 재시도한다.
    SerialProtocol::sendResultTimeout();
    enterTimedMessage(MessageKind::COMMUNICATION_ERROR,
                      CaptureAuthPolicy::retryState(captureMode));
    return;
  }

  if (state == DeviceState::TIMED_MESSAGE &&
      elapsedMs >= messageDurationMs) {
    if (stateAfterMessage == DeviceState::PATTERN_INPUT) {
      enterPatternInput();
    } else if (stateAfterMessage == DeviceState::REGISTRATION_READY) {
      enterRegistrationReady(captureMode);
    } else if (stateAfterMessage == DeviceState::CALIBRATION_READY) {
      enterCalibrationReady();
    } else if (stateAfterMessage == DeviceState::ID_INPUT) {
      enterIdInput();
    } else {
      enterStandby();
    }
    return;
  }

  if (state == DeviceState::AUTH_SUCCESS &&
      elapsedMs >= DOOR_UNLOCK_DURATION_MS) {
    // 인증 성공 후 정확히 20초가 지나면 MOSFET을 끄고 대기 상태로 돌아간다.
    enterStandby();
  }
}
