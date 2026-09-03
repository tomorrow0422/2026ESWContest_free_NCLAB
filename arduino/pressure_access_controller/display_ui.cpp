#include "display_ui.h"

#include <U8g2lib.h>
#include <Wire.h>

#include "config.h"

namespace {

// 기본 OLED: 1.3인치 128x64 SH1106 I2C
// SSD1306을 사용하면 아래 생성자로 교체한다.
// U8G2_SSD1306_128X64_NONAME_F_HW_I2C oled(U8G2_R0, U8X8_PIN_NONE);
U8G2_SH1106_128X64_NONAME_F_HW_I2C oled(U8G2_R0, U8X8_PIN_NONE);

void wake() {
  // 소프트웨어 상태와 실제 OLED 전원 상태가 어긋나더라도 복구한다.
  oled.setPowerSave(0);
}

void beginScreen() {
  wake();
  oled.clearBuffer();
  oled.setDrawColor(1);
}

void finishScreen() {
  oled.sendBuffer();
}

int16_t centeredX(int16_t textWidth) {
  return (textWidth < 128) ? (128 - textWidth) / 2 : 0;
}

void drawKoreanCentered(uint8_t baselineY, const char* text) {
  // korean2 글꼴은 인증 화면에서 사용하는 일반적인 한글 음절을 포함한다.
  oled.setFont(u8g2_font_gulim11_t_korean2);
  const int16_t width = oled.getUTF8Width(text);
  oled.drawUTF8(centeredX(width), baselineY, text);
}

void drawBottomInstructions() {
  oled.setFont(u8g2_font_gulim11_t_korean2);
  const char* instruction = "확인:*  초기화:#";
  const int16_t width = oled.getUTF8Width(instruction);
  // 64픽셀 화면에서 한글 글꼴의 아래쪽 획이 잘리지 않도록 여백을 확보한다.
  oled.drawUTF8(centeredX(width), 60, instruction);
}

}  // namespace

namespace DisplayUi {

void begin() {
  // U8g2는 7비트 I2C 주소를 왼쪽으로 한 비트 이동한 값을 사용한다.
  oled.setI2CAddress(OLED_I2C_ADDRESS << 1);
  oled.setBusClock(100000UL);
  oled.begin();
  Wire.setClock(100000UL);
  oled.setFontMode(1);
  oled.setPowerSave(1);
}

void sleep() {
  oled.setPowerSave(1);
}

void showIdInput(const char* userId, uint8_t userIdLength,
                 bool cursorVisible) {
  beginScreen();
  oled.setFont(u8g2_font_6x12_tr);
  oled.drawStr(0, 17, "User ID: ");

  char maskedId[MAX_ID_DIGITS + 2] = {};
  size_t outputLength = 0;

  for (uint8_t i = 0; i < userIdLength; ++i) {
    // 마지막으로 입력한 숫자만 보여주고 앞 숫자는 '*'로 가린다.
    maskedId[outputLength++] =
        (i + 1 == userIdLength) ? userId[i] : '*';
  }

  maskedId[outputLength++] = cursorVisible ? '_' : ' ';
  maskedId[outputLength] = '\0';
  oled.drawStr(54, 17, maskedId);

  oled.drawHLine(0, 25, 128);
  drawBottomInstructions();
  finishScreen();
}

void showPatternInput(CaptureMode mode) {
  beginScreen();
  if (mode == CaptureMode::CALIBRATION) {
    drawKoreanCentered(18, "캘리브레이션 중");
    drawKoreanCentered(36, "센서를 누르지 마세요.");
  } else if (mode == CaptureMode::REGISTRATION_FIRST) {
    drawKoreanCentered(18, "1차 패턴을");
    drawKoreanCentered(36, "입력해주세요.");
  } else if (mode == CaptureMode::REGISTRATION_SECOND) {
    drawKoreanCentered(18, "2차 패턴을");
    drawKoreanCentered(36, "입력해주세요.");
  } else {
    drawKoreanCentered(18, "압력패턴을");
    drawKoreanCentered(36, "입력해주세요.");
  }
  oled.drawHLine(0, 44, 128);
  if (mode != CaptureMode::CALIBRATION) {
    drawBottomInstructions();
  }
  finishScreen();
}

void showRegistrationReady(CaptureMode mode) {
  beginScreen();
  drawKoreanCentered(18, mode == CaptureMode::REGISTRATION_FIRST
                             ? "1차 패턴 준비"
                             : "2차 패턴 준비");
  drawKoreanCentered(38, "*을 눌러 시작하세요.");
  finishScreen();
}

void showCalibrationReady() {
  beginScreen();
  drawKoreanCentered(18, "센서를 누르지 마세요.");
  drawKoreanCentered(40, "3초 후 자동 시작");
  finishScreen();
}

void showIdWait() {
  beginScreen();
  drawKoreanCentered(35, "사용자 확인중...");
  finishScreen();
}

void showCaptureResultWait(CaptureMode mode) {
  beginScreen();
  if (mode == CaptureMode::CALIBRATION) {
    drawKoreanCentered(35, "캘리브레이션 처리중...");
  } else if (mode == CaptureMode::REGISTRATION_FIRST ||
             mode == CaptureMode::REGISTRATION_SECOND) {
    drawKoreanCentered(35, "등록 패턴 처리중...");
  } else {
    drawKoreanCentered(35, "사용자 식별중...");
  }
  finishScreen();
}

void showAuthenticationSuccess() {
  beginScreen();
  drawKoreanCentered(24, "인증 성공");
  drawKoreanCentered(46, "문이 열렸습니다.");
  finishScreen();
}

void showMessage(MessageKind message) {
  beginScreen();

  switch (message) {
    case MessageKind::CALIBRATION_REQUIRED:
      drawKoreanCentered(24, "캘리브레이션이");
      drawKoreanCentered(46, "필요합니다.");
      break;

    case MessageKind::UNREGISTERED_USER:
      drawKoreanCentered(24, "등록되지 않은");
      drawKoreanCentered(46, "사용자입니다.");
      break;

    case MessageKind::WRONG_PATTERN:
      drawKoreanCentered(24, "압력패턴이");
      drawKoreanCentered(46, "틀렸습니다.");
      break;

    case MessageKind::NOT_USER:
      drawKoreanCentered(24, "사용자가");
      drawKoreanCentered(46, "아닙니다.");
      break;

    case MessageKind::COMMUNICATION_ERROR:
      drawKoreanCentered(24, "통신 오류");
      drawKoreanCentered(46, "다시 시도하세요.");
      break;

    case MessageKind::PI_ERROR:
      drawKoreanCentered(24, "처리 오류");
      drawKoreanCentered(46, "다시 시도하세요.");
      break;

    case MessageKind::PATTERN_DATA_ERROR:
      drawKoreanCentered(24, "데이터 수신 오류");
      drawKoreanCentered(46, "다시 입력하세요.");
      break;

    case MessageKind::PATTERN_TIMEOUT:
      drawKoreanCentered(24, "입력 시간 초과");
      drawKoreanCentered(46, "다시 시도하세요.");
      break;

    case MessageKind::AUTH_PATTERN_NOT_ENTERED:
      drawKoreanCentered(24, "인증 패턴이");
      drawKoreanCentered(46, "입력되지 않았습니다.");
      break;

    case MessageKind::REGISTRATION_PATTERN_NOT_ENTERED:
      drawKoreanCentered(24, "등록 패턴이");
      drawKoreanCentered(46, "입력되지 않았습니다.");
      break;

    case MessageKind::PATTERN_INPUT_CANCELLED:
      drawKoreanCentered(24, "3회 연속 미입력");
      drawKoreanCentered(46, "작업을 취소합니다.");
      break;

    case MessageKind::OPERATION_CANCELLED:
      drawKoreanCentered(24, "작업을");
      drawKoreanCentered(46, "취소했습니다.");
      break;

    case MessageKind::CALIBRATION_SUCCESS:
      drawKoreanCentered(24, "캘리브레이션 완료");
      drawKoreanCentered(46, "측정값을 저장했습니다.");
      break;

    case MessageKind::CAPTURE_FAILED:
      drawKoreanCentered(24, "캡처 처리 실패");
      drawKoreanCentered(46, "다시 시작하세요.");
      break;

    case MessageKind::REGISTRATION_SUCCESS:
      drawKoreanCentered(24, "등록 완료");
      drawKoreanCentered(46, "패턴을 저장했습니다.");
      break;

    case MessageKind::REGISTRATION_RETRY:
      drawKoreanCentered(24, "2차 패턴 불일치");
      drawKoreanCentered(46, "다시 입력하세요.");
      break;

    case MessageKind::REGISTRATION_RESTART:
      drawKoreanCentered(24, "3회 불일치");
      drawKoreanCentered(46, "1차부터 다시 시작");
      break;
  }

  finishScreen();
}

}  // namespace DisplayUi
