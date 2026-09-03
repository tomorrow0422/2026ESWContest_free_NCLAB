"""Parsing and formatting for the Arduino line protocol.

Formatters return newline-free ASCII strings. Adding the line terminator and
encoding the message are transport responsibilities.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from ..identity import validate_user_id
from ..preprocessing import ADC_MAX, ADC_MIN


MAX_PATTERN_DURATION_US = 30_000_000
MAX_PATTERN_SAMPLES = 6000
MAX_CONSECUTIVE_SAMPLES = 255
MAX_UINT32 = (1 << 32) - 1
MAX_MESSAGE_BYTES = 63
MAX_INACTIVITY_REMAINING_ATTEMPTS = 2
CAPTURE_CANCEL_REASONS = frozenset({"PI", "USER", "INACTIVITY"})
DEVICE_MODES = frozenset(
    {
        "IDLE",
        "AUTHENTICATION",
        "REGISTRATION_FIRST",
        "REGISTRATION_SECOND",
        "CALIBRATION",
        "PRESSURE_MONITOR",
        "AUTH_SUCCESS",
    }
)
PATTERN_ERROR_REASONS = frozenset(
    {
        "MALFORMED_SAMPLE",
        "ADC_OUT_OF_RANGE",
        "INVALID_TIMESTAMP",
        "PATTERN_TOO_LONG",
        "TOO_MANY_SAMPLES",
        "MALFORMED_PATTERN_END",
        "EMPTY_PATTERN",
        "SAMPLE_COUNT_MISMATCH",
    }
)


class ProtocolMessageError(ValueError):
    """Report a malformed or out-of-range Arduino protocol message."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class PressureSample:
    """One validated pressure sample from the Arduino."""

    elapsed_us: int
    adc_value: int


@dataclass(frozen=True)
class PressureMonitorSample:
    """One validated sample from the administrator pressure monitor."""

    elapsed_us: int
    adc_value: int


@dataclass(frozen=True)
class PressureConfiguration:
    """One validated pressure-threshold configuration."""

    active_adc: int
    consecutive: int


@dataclass(frozen=True)
class DeviceStatus:
    """Validated Arduino session, door, and pressure-configuration state."""

    mode: str
    door: str
    pressure: str


@dataclass(frozen=True)
class PatternAbort:
    """Validated reason and optional retry count from ``PATTERN_ABORT``."""

    reason: str
    remaining_attempts: int | None = None


class MessageTransport(Protocol):
    """Line-oriented transport required by the protocol client."""

    def read_message(self, timeout: float | None = None) -> str: ...

    def write_message(self, message: str) -> None: ...


class ArduinoProtocolClient:
    """Expose validated Arduino commands over a line-message transport."""

    def __init__(
        self,
        transport: MessageTransport,
        notifier: Callable[[str], None] = print,
    ) -> None:
        self.transport = transport
        self._notifier = notifier

    def read_message(self, timeout: float | None = None) -> str:
        return self.transport.read_message(timeout)

    def send_id_result(self, registered: bool) -> None:
        self.transport.write_message(format_id_result(registered))

    def send_auth_result(
        self,
        authenticated: bool,
        remaining_attempts: int | None = None,
    ) -> None:
        self.transport.write_message(format_auth_result(authenticated, remaining_attempts))

    def send_pattern_error(self, reason: str) -> None:
        self.transport.write_message(format_pattern_error(reason))

    def send_error(self, reason: str) -> None:
        self.transport.write_message(format_processing_error(reason))

    def request_capture(self, operation: str, pass_number: int | None = None) -> None:
        self.transport.write_message(format_capture_request(operation, pass_number))

    def send_capture_ok(self, operation: str) -> None:
        self.transport.write_message(format_capture_ok(operation))

    def send_capture_fail(self, operation: str) -> None:
        self.transport.write_message(format_capture_fail(operation))

    def send_registration_retry(self, remaining_attempts: int) -> None:
        self.transport.write_message(format_registration_retry(remaining_attempts))

    def send_registration_restart(self) -> None:
        self.transport.write_message(format_registration_restart())

    def send_pressure_config(self, active_adc: int, consecutive: int) -> None:
        self.transport.write_message(format_pressure_config(active_adc, consecutive))

    def send_calibration_required(self) -> None:
        self.transport.write_message(format_calibration_required())

    def send_capture_cancel(self) -> None:
        self.transport.write_message(format_capture_cancel())

    def request_status(self) -> None:
        self.transport.write_message(format_status_request())

    def send_ping(self) -> None:
        self.transport.write_message(format_ping())

    def start_pressure_monitor(self) -> None:
        self.transport.write_message(format_pressure_monitor_start())

    def stop_pressure_monitor(self) -> None:
        self.transport.write_message(format_pressure_monitor_stop())

    def notify(self, message: str) -> None:
        self._notifier(message)


def parse_ascii_decimal(value: str) -> int:
    """Parse one canonical unsigned decimal protocol field."""
    if not value or not value.isascii() or not value.isdecimal():
        raise ValueError("protocol integer fields must contain only ASCII digits")
    return int(value)


def parse_id_check(message: str) -> str:
    """Return the unchanged user ID from an ``ID_CHECK`` message."""
    fields = message.split(",")
    if len(fields) != 2 or fields[0] != "ID_CHECK":
        raise ValueError("malformed ID_CHECK message")
    user_id = fields[1]
    validate_user_id(user_id)
    return user_id


def parse_pressure_sample(message: str) -> PressureSample:
    """Parse and range-check one ``P,<elapsed_us>,<adc>`` message."""
    fields = message.split(",")
    try:
        if len(fields) != 3 or fields[0] != "P":
            raise ValueError
        elapsed_us = parse_ascii_decimal(fields[1])
        adc_value = parse_ascii_decimal(fields[2])
    except ValueError as error:
        raise ProtocolMessageError("MALFORMED_SAMPLE", message) from error
    if not ADC_MIN <= adc_value <= ADC_MAX:
        raise ProtocolMessageError("ADC_OUT_OF_RANGE", message)
    if elapsed_us > MAX_PATTERN_DURATION_US:
        raise ProtocolMessageError("PATTERN_TOO_LONG", message)
    return PressureSample(elapsed_us, adc_value)


def parse_pressure_monitor_sample(message: str) -> PressureMonitorSample:
    """Parse one ``M,<elapsed_us>,<adc>`` monitor sample."""
    fields = message.split(",")
    try:
        if len(fields) != 3 or fields[0] != "M":
            raise ValueError
        elapsed_us = parse_ascii_decimal(fields[1])
        adc_value = parse_ascii_decimal(fields[2])
    except ValueError as error:
        raise ProtocolMessageError("MALFORMED_MONITOR_SAMPLE", message) from error
    if elapsed_us > MAX_UINT32:
        raise ProtocolMessageError("MONITOR_TIMESTAMP_OUT_OF_RANGE", message)
    if not ADC_MIN <= adc_value <= ADC_MAX:
        raise ProtocolMessageError("ADC_OUT_OF_RANGE", message)
    return PressureMonitorSample(elapsed_us, adc_value)


def parse_pressure_monitor_started(message: str) -> None:
    """Validate the Arduino acknowledgement for a started monitor."""
    if message != "PRESSURE_MONITOR_STARTED":
        raise ValueError("malformed PRESSURE_MONITOR_STARTED message")


def parse_pressure_monitor_stopped(message: str) -> None:
    """Validate the Arduino acknowledgement for a stopped monitor."""
    if message != "PRESSURE_MONITOR_STOPPED":
        raise ValueError("malformed PRESSURE_MONITOR_STOPPED message")


def parse_pressure_monitor_rejected(message: str) -> str:
    """Return the validated pressure-monitor rejection reason."""
    fields = message.split(",")
    if fields != ["PRESSURE_MONITOR_REJECTED", "BUSY"]:
        raise ValueError("malformed PRESSURE_MONITOR_REJECTED message")
    return "BUSY"


def parse_pattern_end(message: str) -> int:
    """Parse and range-check ``PATTERN_END,<sent_count>``."""
    fields = message.split(",")
    try:
        if len(fields) != 2 or fields[0] != "PATTERN_END":
            raise ValueError
        sent_sample_count = parse_ascii_decimal(fields[1])
        if sent_sample_count > MAX_PATTERN_SAMPLES:
            raise ValueError
    except ValueError as error:
        raise ProtocolMessageError("MALFORMED_PATTERN_END", message) from error
    return sent_sample_count


def parse_pressure_configured(message: str) -> PressureConfiguration:
    """Return the values echoed by an applied pressure-config acknowledgement."""
    fields = message.split(",")
    try:
        if len(fields) != 3 or fields[0] != "PRESSURE_CONFIGURED":
            raise ValueError
        active_adc = parse_ascii_decimal(fields[1])
        consecutive = parse_ascii_decimal(fields[2])
        if not 1 <= active_adc <= ADC_MAX:
            raise ValueError
        if not 1 <= consecutive <= MAX_CONSECUTIVE_SAMPLES:
            raise ValueError
    except ValueError as error:
        raise ValueError("malformed PRESSURE_CONFIGURED message") from error
    return PressureConfiguration(active_adc, consecutive)


def parse_pattern_abort(message: str) -> PatternAbort:
    """Parse the existing total timeout and the inactivity retry message."""
    fields = message.split(",")
    if fields == ["PATTERN_ABORT", "TIMEOUT"]:
        return PatternAbort("TIMEOUT")
    try:
        if len(fields) != 3 or fields[:2] != ["PATTERN_ABORT", "INACTIVITY"]:
            raise ValueError
        remaining_attempts = parse_ascii_decimal(fields[2])
        if not 1 <= remaining_attempts <= MAX_INACTIVITY_REMAINING_ATTEMPTS:
            raise ValueError
    except ValueError as error:
        raise ValueError("malformed PATTERN_ABORT message") from error
    return PatternAbort("INACTIVITY", remaining_attempts)


def parse_capture_cancelled(message: str) -> str:
    """Return the validated reason from ``CAPTURE_CANCELLED,<reason>``."""
    fields = message.split(",")
    if len(fields) != 2 or fields[0] != "CAPTURE_CANCELLED":
        raise ValueError("malformed CAPTURE_CANCELLED message")
    reason = fields[1]
    if reason not in CAPTURE_CANCEL_REASONS:
        raise ValueError("unknown CAPTURE_CANCELLED reason")
    return reason


def parse_status(message: str) -> DeviceStatus:
    """Parse ``STATUS,<mode>,<door>,<pressure>`` from the Arduino."""
    fields = message.split(",")
    if (
        len(fields) != 4
        or fields[0] != "STATUS"
        or fields[1] not in DEVICE_MODES
        or fields[2] not in {"LOCKED", "UNLOCKED"}
        or fields[3] not in {"CONFIGURED", "REQUIRED"}
    ):
        raise ValueError("malformed STATUS message")
    return DeviceStatus(fields[1], fields[2], fields[3])


def format_id_result(registered: bool) -> str:
    if not isinstance(registered, bool):
        raise ValueError("ID 조회 결과는 bool 값이어야 합니다.")
    return "ID_OK" if registered else "ID_NOT_FOUND"


def format_auth_result(authenticated: bool, remaining_attempts: int | None = None) -> str:
    if not isinstance(authenticated, bool):
        raise ValueError("인증 결과는 bool 값이어야 합니다.")
    if authenticated:
        return "AUTH_OK"
    if (
        not isinstance(remaining_attempts, int)
        or isinstance(remaining_attempts, bool)
        or not 0 <= remaining_attempts <= 2
    ):
        raise ValueError("인증 실패 응답에는 0~2의 정수 남은 시도 횟수가 필요합니다.")
    return f"AUTH_FAIL,{remaining_attempts}"


def format_pattern_error(reason: str) -> str:
    if reason not in PATTERN_ERROR_REASONS:
        raise ValueError(f"알 수 없는 패턴 오류 사유입니다: {reason}")
    return f"PATTERN_ERROR,{reason}"


def format_processing_error(reason: str) -> str:
    if reason != "AUTH_PROCESSING":
        raise ValueError(f"알 수 없는 처리 오류 사유입니다: {reason}")
    return f"ERROR,{reason}"


def format_capture_request(operation: str, pass_number: int | None = None) -> str:
    if operation == "CALIBRATION" and pass_number is None:
        return "CAPTURE_REQUEST,CALIBRATION"
    if (
        operation == "REGISTRATION"
        and isinstance(pass_number, int)
        and not isinstance(pass_number, bool)
        and pass_number in {1, 2}
    ):
        return f"CAPTURE_REQUEST,REGISTRATION,{pass_number}"
    raise ValueError(f"잘못된 캡처 요청입니다: {operation}, {pass_number}")


def format_capture_ok(operation: str) -> str:
    _validate_capture_operation(operation)
    return f"CAPTURE_OK,{operation}"


def format_capture_fail(operation: str) -> str:
    _validate_capture_operation(operation)
    return f"CAPTURE_FAIL,{operation},PROCESSING"


def format_registration_retry(remaining_attempts: int) -> str:
    if (
        not isinstance(remaining_attempts, int)
        or isinstance(remaining_attempts, bool)
        or not 1 <= remaining_attempts <= 2
    ):
        raise ValueError("남은 2차 등록 시도 횟수는 1~2여야 합니다.")
    return f"CAPTURE_RETRY,REGISTRATION,2,{remaining_attempts}"


def format_registration_restart() -> str:
    return "CAPTURE_RESTART,REGISTRATION,1"


def format_pressure_config(active_adc: int, consecutive: int) -> str:
    if (
        not isinstance(active_adc, int)
        or isinstance(active_adc, bool)
        or not 1 <= active_adc <= ADC_MAX
    ):
        raise ValueError(f"유효 압력 ADC 기준은 1~{ADC_MAX}의 정수여야 합니다.")
    if (
        not isinstance(consecutive, int)
        or isinstance(consecutive, bool)
        or not 1 <= consecutive <= MAX_CONSECUTIVE_SAMPLES
    ):
        raise ValueError(f"연속 샘플 수는 1~{MAX_CONSECUTIVE_SAMPLES}의 정수여야 합니다.")
    return f"PRESSURE_CONFIG,{active_adc},{consecutive}"


def format_calibration_required() -> str:
    return "CALIBRATION_REQUIRED"


def format_capture_cancel() -> str:
    return "CAPTURE_CANCEL"


def format_status_request() -> str:
    return "STATUS_REQUEST"


def format_ping() -> str:
    return "PING"


def format_pressure_monitor_start() -> str:
    return "PRESSURE_MONITOR_START"


def format_pressure_monitor_stop() -> str:
    return "PRESSURE_MONITOR_STOP"


def _validate_capture_operation(operation: str) -> None:
    if operation not in {"CALIBRATION", "REGISTRATION"}:
        raise ValueError(f"잘못된 캡처 작업입니다: {operation}")
