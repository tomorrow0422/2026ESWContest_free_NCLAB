# Arduino–Raspberry Pi 시리얼 프로토콜

Arduino 인증 단말기와 Raspberry Pi 관리자·인증 애플리케이션은 USB 시리얼로 통신합니다. 이 문서는 실제 운영에 사용하는 메시지만 정리합니다.

## 기본 전송 규칙

- 기본 보드레이트: `115200bps`
- 모든 메시지는 ASCII 텍스트 한 줄이며 `\n`으로 끝납니다.
- 필드는 쉼표(`,`)로 구분합니다.
- Arduino 수신 메시지는 줄바꿈을 제외하고 최대 63바이트입니다.
- 숫자 필드는 부호나 공백 없이 ASCII 숫자 `0`~`9`만 사용합니다.
- User ID는 숫자 모양의 문자열로 취급하므로 `0001`처럼 앞자리 0을 보존합니다.

## Arduino → Raspberry Pi

| 메시지 | 의미 |
|---|---|
| `BOOT,READY` | Arduino 부팅 완료 및 새 세션 시작 알림 |
| `ID_CHECK,<user_id>` | 입력한 User ID의 등록 여부 조회 |
| `PATTERN_START` | 압력 패턴 수집 시작 |
| `P,<elapsed_us>,<adc>` | 인증·등록·캘리브레이션용 압력 샘플 |
| `PATTERN_RESET` | 현재 압력 패턴을 처음부터 다시 수집 |
| `PATTERN_END,<sent_count>` | 압력 패턴 수집 종료 및 전송 샘플 수 보고 |
| `PATTERN_ABORT,TIMEOUT` | 최대 입력 시간 초과로 현재 패턴 폐기 |
| `PATTERN_ABORT,INACTIVITY,<remaining>` | 압력 미입력으로 현재 패턴을 폐기하고 재시도 |
| `PRESSURE_CONFIGURED,<active_adc>,<consecutive>` | 유효 압력 기준 적용 완료 |
| `PRESSURE_MONITOR_STARTED` | 관리자 센서 모니터링 시작 확인 |
| `M,<elapsed_us>,<adc>` | 관리자 센서 모니터링용 압력 샘플 |
| `PRESSURE_MONITOR_STOPPED` | 관리자 센서 모니터링 종료 확인 |
| `PRESSURE_MONITOR_REJECTED,BUSY` | 다른 작업 중이어서 모니터링 시작 거부 |
| `CAPTURE_CANCELLED,PI` | Raspberry Pi 요청에 따른 작업 취소 완료 |
| `CAPTURE_CANCELLED,USER` | 사용자의 긴 `#` 입력으로 작업 취소 |
| `CAPTURE_CANCELLED,INACTIVITY` | 연속 압력 미입력으로 작업 취소 |
| `STATUS,<mode>,<door>,<pressure>` | 현재 단말기 상태 보고 |
| `TIMEOUT,ID` | ID 확인 응답 대기 시간 초과 |
| `TIMEOUT,RESULT` | 인증·등록·캘리브레이션 결과 대기 시간 초과 |
| `DOOR,UNLOCKED` | 잠금장치 해제 상태 보고 |
| `DOOR,LOCKED` | 잠금장치 잠금 상태 보고 |
| `CAPTURE_REJECTED,BUSY` | 현재 상태에서 새 수집 요청을 수행할 수 없음 |
| `ERROR,RX_OVERFLOW` | Arduino 시리얼 수신 버퍼 초과 |
| `PONG` | `PING`에 대한 연결 확인 응답 |

## Raspberry Pi → Arduino

| 메시지 | 의미 |
|---|---|
| `ID_OK` | 등록된 User ID |
| `ID_NOT_FOUND` | 등록되지 않은 User ID |
| `AUTH_OK` | 압력 패턴 인증 성공 |
| `AUTH_FAIL,<remaining>` | 인증 실패 및 남은 인증 시도 횟수 |
| `PATTERN_ERROR,<reason>` | 압력 스트림 형식·범위·무결성 오류 |
| `ERROR,AUTH_PROCESSING` | Raspberry Pi 인증 처리 오류 |
| `CAPTURE_REQUEST,CALIBRATION` | 무압력 상태 캘리브레이션 수집 요청 |
| `CAPTURE_REQUEST,REGISTRATION,1` | 사용자 등록 1차 패턴 수집 요청 |
| `CAPTURE_REQUEST,REGISTRATION,2` | 사용자 등록 2차 패턴 수집 요청 |
| `CAPTURE_RETRY,REGISTRATION,2,<remaining>` | 1차 패턴을 유지하고 2차 패턴 재입력 요청 |
| `CAPTURE_RESTART,REGISTRATION,1` | 기존 등록 패턴을 폐기하고 1차부터 재시작 |
| `CAPTURE_OK,CALIBRATION` | 캘리브레이션 처리 성공 |
| `CAPTURE_OK,REGISTRATION` | 사용자 등록 성공 |
| `CAPTURE_FAIL,<operation>,PROCESSING` | 등록 또는 캘리브레이션 처리 실패 |
| `PRESSURE_CONFIG,<active_adc>,<consecutive>` | 캘리브레이션으로 계산한 유효 압력 기준 설정 |
| `CALIBRATION_REQUIRED` | 캘리브레이션이 필요함을 단말기에 알림 |
| `CAPTURE_CANCEL` | 현재 임시 세션을 종료하고 안전한 대기 상태로 전환 요청 |
| `STATUS_REQUEST` | 현재 Arduino 상태 조회 |
| `PRESSURE_MONITOR_START` | 관리자 센서 모니터링 시작 요청 |
| `PRESSURE_MONITOR_STOP` | 관리자 센서 모니터링 종료 요청 |
| `PING` | 연결 상태 확인 요청 |

## 주요 필드

| 필드 | 형식 및 범위 |
|---|---|
| `user_id` | ASCII 숫자 문자열, 1~10자리 |
| `elapsed_us` | 패턴 또는 모니터 시작 후 경과 시간(µs) |
| `adc` | 14비트 ADC 값 `0~16383` |
| `sent_count` | Arduino가 전송한 패턴 샘플 수 |
| `remaining` | 남은 인증 또는 재시도 횟수 |
| `active_adc` | 유효 압력으로 인정할 최소 ADC 값 |
| `consecutive` | 유효 압력 판정에 필요한 연속 샘플 수 |

인증·등록·캘리브레이션의 `P` 샘플은 최대 30초 동안 수집합니다. `elapsed_us`는 개별 샘플 간격이 아니라 수집 시작 이후의 누적 경과 시간이며, Raspberry Pi 전처리에서 실제 입력 시간 흐름을 복원하는 데 사용합니다.

## 연결 초기화와 상태 동기화

Raspberry Pi는 시리얼 포트를 열었다는 이유만으로 두 장치의 상태가 일치한다고 가정하지 않습니다. 새 연결에서는 먼저 Arduino의 임시 작업 상태를 정리하고 현재 상태를 확인합니다.

```text
Raspberry Pi -> Arduino    CAPTURE_CANCEL
Arduino -> Raspberry Pi    CAPTURE_CANCELLED,PI
Raspberry Pi -> Arduino    STATUS_REQUEST
Arduino -> Raspberry Pi    STATUS,IDLE,LOCKED,<pressure>
Raspberry Pi               저장된 캘리브레이션 기준 동기화
```

Arduino가 재부팅해 `BOOT,READY`를 전송한 경우에도 Raspberry Pi는 같은 동기화 절차를 다시 수행합니다. 이 과정은 인증이나 등록 도중 장치가 재연결되어 이전 세션의 압력 샘플이나 잠금 상태가 다음 세션에 섞이는 것을 방지합니다.

## 상태 값

`STATUS`의 `<mode>`는 다음 값을 사용합니다.

- `IDLE`: 새 작업을 시작할 수 있는 대기 상태
- `AUTHENTICATION`: 사용자 인증 진행 중
- `REGISTRATION_FIRST`: 등록 1차 패턴 수집 진행 중
- `REGISTRATION_SECOND`: 등록 2차 패턴 수집 진행 중
- `CALIBRATION`: 센서 캘리브레이션 진행 중
- `PRESSURE_MONITOR`: 관리자 센서 모니터링 진행 중
- `AUTH_SUCCESS`: 인증 성공 후 잠금장치가 열린 상태

`<door>`는 `LOCKED` 또는 `UNLOCKED`, `<pressure>`는 `CONFIGURED` 또는 `REQUIRED`를 사용합니다.

## 인증 흐름 예시

```text
사용자 -> Arduino          User ID 입력
Arduino -> Raspberry Pi    ID_CHECK,0001
Raspberry Pi -> Arduino    ID_OK
Arduino -> Raspberry Pi    PATTERN_START
Arduino -> Raspberry Pi    P,0,120
Arduino -> Raspberry Pi    P,5000,245
...                         ...
Arduino -> Raspberry Pi    PATTERN_END,<count>
Raspberry Pi               전처리 -> AI 임베딩 추론 -> 등록 임베딩과 비교
Raspberry Pi -> Arduino    AUTH_OK
Arduino                    잠금장치 해제
Arduino -> Raspberry Pi    DOOR,UNLOCKED
```

인증 실패 시 Raspberry Pi는 `AUTH_FAIL,<remaining>`을 전송하고 Arduino는 사용자에게 재입력을 안내합니다.

## 캘리브레이션과 등록

센서 캘리브레이션은 Raspberry Pi 관리자가 요청하며, Arduino는 무압력 상태를 일정 시간 수집합니다. 계산된 유효 압력 기준은 `PRESSURE_CONFIG`로 다시 Arduino에 전달됩니다.

사용자 등록은 두 번의 압력 패턴을 수집합니다. Raspberry Pi는 각 패턴을 전처리하고 임베딩으로 변환한 뒤 두 패턴이 충분히 유사한지 확인합니다. 등록이 완료되면 두 임베딩의 평균을 정규화해 해당 사용자의 기준 패턴으로 저장합니다.
