"""Stateful Arduino authentication, registration, calibration, and monitor sessions."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import replace
from threading import Event, RLock
from typing import Any, Callable, Protocol

from ..authentication import AuthenticationLockedError
from ..calibration_data import CalibrationRecord
from ..identity import validate_user_id
from ..preprocessing import SignalPattern
from ..storage.errors import StorageError
from .protocol import (
    DeviceStatus,
    MAX_PATTERN_SAMPLES,
    PressureMonitorSample,
    ProtocolMessageError,
    parse_capture_cancelled,
    parse_id_check,
    parse_pattern_abort,
    parse_pattern_end,
    parse_pressure_configured,
    parse_pressure_monitor_rejected,
    parse_pressure_monitor_sample,
    parse_pressure_monitor_started,
    parse_pressure_monitor_stopped,
    parse_pressure_sample,
    parse_status,
)
from .transport import CommunicationError, CommunicationTimeoutError
from .message_validation import is_valid_activity_message
from .session_state import (
    PendingDeviceStatus,
    PendingPressureConfiguration,
    PendingSessionReset,
    RegistrationRequest,
)


MAX_AUTH_ATTEMPTS = 3
MAX_SECOND_REGISTRATION_ATTEMPTS = 3
SESSION_RESET_ACK_TIMEOUT_SECONDS = 2.0
MAX_SESSION_RESET_ATTEMPTS = 3
SESSION_STATUS_TIMEOUT_SECONDS = 2.0
MAX_SESSION_STATUS_ATTEMPTS = 3
HEARTBEAT_IDLE_SECONDS = 5.0
HEARTBEAT_RESPONSE_TIMEOUT_SECONDS = 2.0
MAX_HEARTBEAT_FAILURES = 3
PRESSURE_CONFIG_ACK_TIMEOUT_SECONDS = 2.0
MAX_PRESSURE_CONFIG_ATTEMPTS = 3
PRESSURE_MONITOR_BUFFER_CAPACITY = 2000
PATTERN_MODES = frozenset(
    {
        "authentication",
        "calibration",
        "registration_first",
        "registration_second",
    }
)
PRESSURE_MONITOR_MODES = frozenset(
    {
        "pressure_monitor_starting",
        "pressure_monitor",
        "pressure_monitor_stopping",
    }
)


class ArduinoSessionDevice(Protocol):
    """High-level Arduino operations required by a pattern session."""

    def read_message(self, timeout: float | None = None) -> str: ...

    def send_id_result(self, registered: bool) -> None: ...

    def send_auth_result(
        self,
        authenticated: bool,
        remaining_attempts: int | None = None,
    ) -> None: ...

    def send_pattern_error(self, reason: str) -> None: ...

    def send_error(self, reason: str) -> None: ...

    def request_capture(self, operation: str, pass_number: int | None = None) -> None: ...

    def send_capture_ok(self, operation: str) -> None: ...

    def send_capture_fail(self, operation: str) -> None: ...

    def send_registration_retry(self, remaining_attempts: int) -> None: ...

    def send_registration_restart(self) -> None: ...

    def send_pressure_config(self, active_adc: int, consecutive: int) -> None: ...

    def send_calibration_required(self) -> None: ...

    def send_capture_cancel(self) -> None: ...

    def request_status(self) -> None: ...

    def send_ping(self) -> None: ...

    def start_pressure_monitor(self) -> None: ...

    def stop_pressure_monitor(self) -> None: ...

    def notify(self, message: str) -> None: ...


class ArduinoPatternSession:
    """Route one Arduino pressure-stream protocol to Pi-owned operations.

    The same ``P,...`` and ``PATTERN_END`` messages are used for
    authentication, two-pass registration, and idle-state calibration.  The
    separate ``M,...`` stream carries administrator monitor samples without
    feeding them into pattern evaluation or storage.
    """

    def __init__(
        self,
        device: ArduinoSessionDevice,
        is_registered: Callable[[str], bool],
        authenticate: Callable[[str, SignalPattern], tuple[bool, float]],
        register: Callable[[RegistrationRequest, SignalPattern, SignalPattern], tuple[bool, float]]
        | None = None,
        calibrate: Callable[[SignalPattern], Any] | None = None,
        get_calibration: Callable[[], CalibrationRecord | None] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        synchronize_connection: bool = False,
        get_authentication_failure_status: Callable[[str], tuple[int, int]] | None = None,
    ) -> None:
        self.device = device
        self.is_registered = is_registered
        self.authenticate = authenticate
        self.register = register
        self.calibrate = calibrate
        self.get_calibration = get_calibration
        self._monotonic = monotonic
        self._synchronize_connection = synchronize_connection
        self.get_authentication_failure_status = get_authentication_failure_status
        self._active_user_id: str | None = None
        self._mode = "idle"
        self._registration_request: RegistrationRequest | None = None
        self._first_registration_pattern: SignalPattern | None = None
        self._second_registration_attempts = 0
        self._stop_event = Event()
        self._state_lock = RLock()
        self._failure: CommunicationError | None = None
        self._samples: list[tuple[int, int]] = []
        self._last_timestamp_us: int | None = None
        self._invalid_pattern_reason: str | None = None
        self._pattern_active = False
        self._authentication_failures = 0
        self._last_successful_similarity: float | None = None
        self._pending_session_reset: PendingSessionReset | None = None
        self._pending_device_status: PendingDeviceStatus | None = None
        self._last_device_status: DeviceStatus | None = None
        self._pending_pressure_config: PendingPressureConfiguration | None = None
        self._heartbeat_enabled = False
        self._last_valid_message_at = self._monotonic()
        self._heartbeat_deadline: float | None = None
        self._heartbeat_failures = 0
        self._pressure_config_sync_failed = False
        self._pressure_monitor_samples: deque[PressureMonitorSample] = deque(
            maxlen=PRESSURE_MONITOR_BUFFER_CAPACITY
        )

    def _reset_pattern(self) -> None:
        self._samples = []
        self._last_timestamp_us = None
        self._invalid_pattern_reason = None
        self._pattern_active = False

    def enable_connection_synchronization(self) -> None:
        """Run the Arduino reset handshake when the message loop starts."""
        self._synchronize_connection = True

    def enable_heartbeat(self) -> None:
        """Detect an open serial port whose Arduino no longer responds."""
        self._heartbeat_enabled = True

    def set_authentication_failure_status_provider(
        self,
        provider: Callable[[str], tuple[int, int]],
    ) -> None:
        """Use persistent failure and lock state for serial authentication."""
        self.get_authentication_failure_status = provider

    def _reset_operation(self) -> None:
        self._mode = "idle"
        self._active_user_id = None
        self._authentication_failures = 0
        self._last_successful_similarity = None
        self._pending_pressure_config = None
        self._registration_request = None
        self._first_registration_pattern = None
        self._second_registration_attempts = 0
        self._pressure_monitor_samples.clear()
        self._reset_pattern()

    @property
    def failure(self) -> CommunicationError | None:
        with self._state_lock:
            return self._failure

    def _record_failure(self, error: CommunicationError) -> None:
        should_notify = False
        with self._state_lock:
            if self._failure is None:
                self._failure = error
                self._reset_operation()
                self._pending_session_reset = None
                self._pending_device_status = None
                self._heartbeat_deadline = None
                should_notify = True
        if should_notify:
            self.device.notify(f"통신 오류: {error}")

    def raise_if_failed(self) -> None:
        error = self.failure
        if error is not None:
            raise CommunicationError(f"Arduino 통신 서비스가 중단됐습니다: {error}") from error

    def status(self) -> str:
        with self._state_lock:
            if self._failure is not None:
                return f"통신 오류 ({self._failure})"
            if self._pending_session_reset is not None:
                return "Arduino 세션 초기화 확인 대기"
            if self._pending_device_status is not None:
                return "Arduino 상태 확인 대기"
            if self._pending_pressure_config is not None:
                if self._pending_pressure_config.completes_calibration:
                    return "캘리브레이션 압력 설정 확인 대기"
                return "압력 설정 확인 대기"
            labels = {
                "idle": "대기",
                "authentication": f"인증 대기 ({self._active_user_id or '사용자 미지정'})",
                "authentication_succeeded": "인증 완료 후 문 잠금 대기",
                "calibration": "캘리브레이션 수집 대기",
                "registration_first": "등록 1차 패턴 수집 대기",
                "registration_second": "등록 2차 패턴 수집 대기",
                "pressure_monitor_starting": "압력센서 진단 시작 대기",
                "pressure_monitor": "압력센서 진단 중",
                "pressure_monitor_stopping": "압력센서 진단 종료 대기",
            }
            return labels[self._mode]

    def arduino_status(self) -> str:
        """Return the most recently verified Arduino-side state."""
        with self._state_lock:
            status = self._last_device_status
            if status is None:
                return "확인 전"
            return f"{status.mode}/{status.door}/{status.pressure}"

    def has_active_operation(self) -> bool:
        """Return whether registration, authentication, or calibration is active."""
        with self._state_lock:
            return (
                self._mode != "idle"
                or self._pending_session_reset is not None
                or self._pending_device_status is not None
                or self._pending_pressure_config is not None
            )

    def _require_idle(self) -> None:
        self.raise_if_failed()
        if (
            self._mode != "idle"
            or self._pending_session_reset is not None
            or self._pending_device_status is not None
            or self._pending_pressure_config is not None
        ):
            raise ValueError(f"다른 작업이 진행 중입니다: {self.status()}")

    def _request_capture(self, operation: str, pass_number: int | None = None) -> None:
        try:
            self.device.request_capture(operation, pass_number)
        except CommunicationError as error:
            self._record_failure(error)
            raise

    def _send_calibration_required(self) -> None:
        try:
            self.device.send_calibration_required()
        except CommunicationError as error:
            self._record_failure(error)
            raise

    def _send_pressure_config(
        self,
        record: CalibrationRecord,
        *,
        completes_calibration: bool = False,
    ) -> None:
        active_adc = math.floor(record.active_threshold) + 1
        pending = PendingPressureConfiguration(
            active_adc,
            record.min_consecutive_samples,
            completes_calibration,
        )
        self._pending_pressure_config = pending
        self._pressure_config_sync_failed = False
        self._send_pending_pressure_config()

    def _send_pending_pressure_config(self) -> None:
        pending = self._pending_pressure_config
        if pending is None:
            raise RuntimeError("대기 중인 압력 설정이 없습니다.")
        try:
            self.device.send_pressure_config(pending.active_adc, pending.consecutive)
        except CommunicationError as error:
            self._record_failure(error)
            raise
        self._pending_pressure_config = replace(
            pending,
            attempts=pending.attempts + 1,
            deadline=self._monotonic() + PRESSURE_CONFIG_ACK_TIMEOUT_SECONDS,
        )

    def _pressure_config_read_timeout(self) -> float | None:
        pending = self._pending_pressure_config
        if pending is None:
            return None
        return max(0.0, pending.deadline - self._monotonic())

    def _handle_pressure_config_timeout(self) -> None:
        pending = self._pending_pressure_config
        if pending is None:
            return
        if pending.attempts < MAX_PRESSURE_CONFIG_ATTEMPTS:
            self.device.notify(
                "압력 설정 확인 시간이 초과되어 다시 전송합니다. "
                f"({pending.attempts + 1}/{MAX_PRESSURE_CONFIG_ATTEMPTS})"
            )
            self._send_pending_pressure_config()
            return
        self._pressure_config_sync_failed = True
        self.device.notify("압력 설정 확인을 3회 받지 못해 캘리브레이션 필요 상태를 유지합니다.")
        self._send_calibration_required()
        self._reset_operation()

    def _send_capture_cancel(self) -> None:
        try:
            self.device.send_capture_cancel()
        except CommunicationError as error:
            self._record_failure(error)
            raise

    def _begin_session_reset(self) -> None:
        self._reset_operation()
        self._pending_device_status = None
        self._pending_session_reset = PendingSessionReset()
        self._send_pending_session_reset()

    def _send_pending_session_reset(self) -> None:
        pending = self._pending_session_reset
        if pending is None:
            raise RuntimeError("대기 중인 세션 초기화가 없습니다.")
        self._send_capture_cancel()
        self._pending_session_reset = replace(
            pending,
            attempts=pending.attempts + 1,
            deadline=self._monotonic() + SESSION_RESET_ACK_TIMEOUT_SECONDS,
        )

    def _session_reset_read_timeout(self) -> float | None:
        pending = self._pending_session_reset
        if pending is None:
            return None
        return max(0.0, pending.deadline - self._monotonic())

    def _handle_session_reset_timeout(self) -> None:
        pending = self._pending_session_reset
        if pending is None:
            return
        if pending.attempts < MAX_SESSION_RESET_ATTEMPTS:
            self.device.notify(
                "Arduino 세션 초기화 확인 시간이 초과되어 다시 요청합니다. "
                f"({pending.attempts + 1}/{MAX_SESSION_RESET_ATTEMPTS})"
            )
            self._send_pending_session_reset()
            return
        raise CommunicationError("Arduino 세션 초기화 확인을 3회 받지 못했습니다.")

    def _begin_device_status_request(self) -> None:
        self._pending_device_status = PendingDeviceStatus()
        self._send_pending_device_status_request()

    def _send_pending_device_status_request(self) -> None:
        pending = self._pending_device_status
        if pending is None:
            raise RuntimeError("대기 중인 Arduino 상태 조회가 없습니다.")
        try:
            self.device.request_status()
        except CommunicationError as error:
            self._record_failure(error)
            raise
        self._pending_device_status = replace(
            pending,
            attempts=pending.attempts + 1,
            deadline=self._monotonic() + SESSION_STATUS_TIMEOUT_SECONDS,
        )

    def _device_status_read_timeout(self) -> float | None:
        pending = self._pending_device_status
        if pending is None:
            return None
        return max(0.0, pending.deadline - self._monotonic())

    def _handle_device_status_timeout(self) -> None:
        pending = self._pending_device_status
        if pending is None:
            return
        if pending.attempts < MAX_SESSION_STATUS_ATTEMPTS:
            self.device.notify(
                "Arduino 상태 확인 시간이 초과되어 다시 요청합니다. "
                f"({pending.attempts + 1}/{MAX_SESSION_STATUS_ATTEMPTS})"
            )
            self._send_pending_device_status_request()
            return
        raise CommunicationError("Arduino 상태 확인을 3회 받지 못했습니다.")

    def _heartbeat_read_timeout(self) -> float | None:
        if not self._heartbeat_enabled or any(
            pending is not None
            for pending in (
                self._pending_session_reset,
                self._pending_device_status,
                self._pending_pressure_config,
            )
        ):
            return None
        deadline = self._heartbeat_deadline
        if deadline is None:
            deadline = self._last_valid_message_at + HEARTBEAT_IDLE_SECONDS
        return max(0.0, deadline - self._monotonic())

    def _send_heartbeat(self) -> None:
        try:
            self.device.send_ping()
        except CommunicationError as error:
            self._record_failure(error)
            raise
        self._heartbeat_deadline = self._monotonic() + HEARTBEAT_RESPONSE_TIMEOUT_SECONDS

    def _handle_heartbeat_timeout(self) -> None:
        if self._heartbeat_deadline is None:
            self._send_heartbeat()
            return
        self._heartbeat_failures += 1
        if self._heartbeat_failures >= MAX_HEARTBEAT_FAILURES:
            raise CommunicationError("Arduino heartbeat 응답을 3회 연속 받지 못했습니다.")
        self.device.notify(
            "Arduino heartbeat 응답 시간이 초과되어 다시 확인합니다. "
            f"({self._heartbeat_failures + 1}/{MAX_HEARTBEAT_FAILURES})"
        )
        self._send_heartbeat()

    def _handle_pong(self) -> None:
        if self._heartbeat_deadline is None:
            return
        self._heartbeat_deadline = None
        self._heartbeat_failures = 0
        self._last_valid_message_at = self._monotonic()

    def _send_pressure_monitor_start(self) -> None:
        try:
            self.device.start_pressure_monitor()
        except CommunicationError as error:
            self._record_failure(error)
            raise

    def _send_pressure_monitor_stop(self) -> None:
        try:
            self.device.stop_pressure_monitor()
        except CommunicationError as error:
            self._record_failure(error)
            raise

    def _synchronize_calibration(self) -> None:
        if self.get_calibration is None:
            return
        record = self.get_calibration()
        if record is None:
            self._send_calibration_required()
        else:
            self._send_pressure_config(record)

    def _require_calibration(self) -> None:
        if self.get_calibration is None:
            return
        if self.get_calibration() is None:
            self._send_calibration_required()
            raise ValueError("먼저 센서 캘리브레이션을 완료하세요.")
        if self._pressure_config_sync_failed:
            self._send_calibration_required()
            raise ValueError("압력 설정 동기화에 실패했습니다. 센서 캘리브레이션을 다시 실행하세요.")

    def begin_calibration(self) -> None:
        with self._state_lock:
            self._require_idle()
            if self.calibrate is None:
                raise ValueError("캘리브레이션 기능이 설정되지 않았습니다.")
            self._mode = "calibration"
            self._reset_pattern()
            self._request_capture("CALIBRATION")
            self.device.notify("[캘리브레이션] 센서를 누르지 마세요. Arduino가 3초 후 자동 측정합니다.")

    def begin_pressure_monitor(self) -> None:
        """Request a raw pressure stream without evaluating or storing it."""
        with self._state_lock:
            self._require_idle()
            self._pressure_monitor_samples.clear()
            self._reset_pattern()
            self._mode = "pressure_monitor_starting"
            self._send_pressure_monitor_start()
            self.device.notify("압력센서 진단 시작을 요청했습니다.")

    def stop_pressure_monitor(self) -> bool:
        """Request the end of the active pressure monitor session."""
        with self._state_lock:
            self.raise_if_failed()
            if self._mode not in PRESSURE_MONITOR_MODES:
                return False
            self._send_pressure_monitor_stop()
            self._mode = "pressure_monitor_stopping"
            self.device.notify("압력센서 진단 종료를 요청했습니다.")
            return True

    def drain_pressure_monitor_samples(self) -> tuple[PressureMonitorSample, ...]:
        """Return buffered monitor samples once, oldest first."""
        with self._state_lock:
            samples = tuple(self._pressure_monitor_samples)
            self._pressure_monitor_samples.clear()
            return samples

    def begin_registration(self, user_id: str, name: str) -> None:
        with self._state_lock:
            self._require_idle()
            self._require_calibration()
            if self.register is None:
                raise ValueError("등록 기능이 설정되지 않았습니다.")
            validate_user_id(user_id)
            if not name:
                raise ValueError("이름을 입력하세요.")
            self._registration_request = RegistrationRequest(user_id, name)
            self._mode = "registration_first"
            self._second_registration_attempts = 0
            self._reset_pattern()
            self._request_capture("REGISTRATION", 1)
            self.device.notify("[등록] 1차 압력 패턴을 입력하세요.")

    def begin_authentication(self, user_id: str) -> None:
        with self._state_lock:
            self._require_idle()
            self._require_calibration()
            validate_user_id(user_id)
            if not self.is_registered(user_id):
                raise ValueError("등록되지 않은 사용자입니다.")
            _, lock_seconds = self._authentication_failure_status(user_id)
            if lock_seconds > 0:
                raise ValueError(f"인증이 잠겨 있습니다. {lock_seconds}초 후에 다시 시도하세요.")
            self._active_user_id = user_id
            self._mode = "authentication"
            self._authentication_failures = 0
            self._last_successful_similarity = None
            self._reset_pattern()
            self.device.notify(f"[인증] {user_id}의 압력 패턴을 입력하세요.")

    def _handle_id_check(self, message: str) -> None:
        if self.get_calibration is not None and self.get_calibration() is None:
            self._reset_operation()
            self.device.notify("인증을 시작하려면 먼저 센서 캘리브레이션이 필요합니다.")
            self._send_calibration_required()
            return
        try:
            user_id = parse_id_check(message)
        except ValueError:
            if self._mode in {"idle", "authentication"}:
                self._reset_operation()
            self.device.notify("아두이노 오류: 잘못된 ID 조회 요청")
            self.device.send_id_result(False)
            return
        if self._mode not in {"idle", "authentication"}:
            self.device.notify(f"아두이노 ID 요청을 무시했습니다: {self.status()}")
            self.device.send_id_result(False)
            return
        _, lock_seconds = self._authentication_failure_status(user_id)
        if lock_seconds > 0:
            self._reset_operation()
            self.device.notify(
                f"인증 요청 거부: {user_id}은(는) {lock_seconds}초 동안 잠겨 있습니다."
            )
            self.device.send_id_result(False)
            return
        self._active_user_id = user_id if self.is_registered(user_id) else None
        self._mode = "authentication" if self._active_user_id else "idle"
        self._authentication_failures = 0
        self._last_successful_similarity = None
        self._reset_pattern()
        self.device.send_id_result(self._active_user_id is not None)

    def _handle_pattern_start(self) -> None:
        if self._mode not in PATTERN_MODES:
            self.device.notify("아두이노 오류: 선택된 등록·인증·캘리브레이션 작업 없이 패턴이 시작됐습니다.")
            return
        if self._pattern_active:
            self.device.notify("경고: 미완료 패턴을 폐기하고 새 패턴을 시작합니다.")
        self._reset_pattern()
        self._pattern_active = True

    def _handle_pattern_reset(self) -> None:
        if not self._pattern_active:
            self.device.notify("아두이노 오류: PATTERN_START 없이 PATTERN_RESET을 받았습니다.")
            return
        self._reset_pattern()
        self._pattern_active = True

    def _invalidate_pattern(self, reason: str, message: str) -> None:
        if self._invalid_pattern_reason is not None:
            return
        self._invalid_pattern_reason = reason
        self.device.notify(f"아두이노 오류: {reason}: {message}")

    def _handle_sample(self, message: str) -> None:
        if self._mode not in PATTERN_MODES:
            self.device.notify("아두이노 오류: 선택된 등록·인증·캘리브레이션 작업 없이 샘플을 받았습니다.")
            return
        if not self._pattern_active:
            self.device.notify(f"아두이노 오류: PATTERN_START 없이 압력 샘플을 받았습니다: {message}")
            return
        if self._invalid_pattern_reason is not None:
            return
        try:
            parsed = parse_pressure_sample(message)
        except ProtocolMessageError as error:
            self._invalidate_pattern(error.reason, message)
            return
        timestamp_us = parsed.elapsed_us
        sample = parsed.adc_value
        if self._last_timestamp_us is not None and timestamp_us <= self._last_timestamp_us:
            self._invalidate_pattern("INVALID_TIMESTAMP", message)
            return
        if len(self._samples) >= MAX_PATTERN_SAMPLES:
            self._invalidate_pattern("TOO_MANY_SAMPLES", message)
            return
        self._samples.append((timestamp_us, sample))
        self._last_timestamp_us = timestamp_us

    def _handle_pattern_end(self, message: str) -> None:
        if not self._pattern_active:
            self.device.notify(f"아두이노 오류: PATTERN_START 없이 패턴 종료를 받았습니다: {message}")
            return
        self._pattern_active = False
        try:
            sent_sample_count = parse_pattern_end(message)
        except ProtocolMessageError:
            sent_sample_count = None
            self._invalidate_pattern("MALFORMED_PATTERN_END", message)
        if not self._samples:
            self._invalidate_pattern("EMPTY_PATTERN", message)
        elif sent_sample_count is not None and sent_sample_count != len(self._samples):
            self._invalidate_pattern(
                "SAMPLE_COUNT_MISMATCH",
                f"Arduino={sent_sample_count}, Pi={len(self._samples)}",
            )
        if self._invalid_pattern_reason is not None:
            self._handle_invalid_pattern()
            return
        pattern = SignalPattern(
            values=tuple(float(sample) for _, sample in self._samples),
            elapsed_us=tuple(timestamp_us for timestamp_us, _ in self._samples),
        )
        if self._mode == "authentication":
            self._finish_authentication(pattern)
        elif self._mode == "calibration":
            self._finish_calibration(pattern)
        elif self._mode == "registration_first":
            self._first_registration_pattern = pattern
            self._mode = "registration_second"
            self._second_registration_attempts = 0
            self._reset_pattern()
            self.device.request_capture("REGISTRATION", 2)
            self.device.notify("[등록] 1차 패턴을 받았습니다. 같은 패턴을 2차로 입력하세요.")
        elif self._mode == "registration_second":
            self._finish_registration(pattern)
        else:
            self.device.notify("아두이노 오류: 처리할 작업 없이 PATTERN_END를 받았습니다.")
            self._reset_pattern()

    def _handle_invalid_pattern(self) -> None:
        reason = self._invalid_pattern_reason or "UNKNOWN_PATTERN_ERROR"
        self.device.notify(f"입력 오류: 손상된 압력 패턴을 받았습니다. ({reason})")
        if self._mode != "idle":
            self.device.send_pattern_error(reason)
            self._reset_pattern()

    def _send_auth_failure(self, *, count_failure: bool) -> None:
        if count_failure:
            self._authentication_failures = min(
                self._authentication_failures + 1,
                MAX_AUTH_ATTEMPTS,
            )
        remaining_attempts = MAX_AUTH_ATTEMPTS - self._authentication_failures
        self.device.send_auth_result(False, remaining_attempts)

    def _authentication_failure_status(self, user_id: str) -> tuple[int, int]:
        if self.get_authentication_failure_status is None:
            return self._authentication_failures, 0
        return self.get_authentication_failure_status(user_id)

    def _finish_authentication(self, pattern: SignalPattern) -> None:
        if self._active_user_id is None:
            self.device.notify("아두이노 오류: ID 확인 전 신호를 받았습니다.")
            self.device.send_error("AUTH_PROCESSING")
            self._reset_pattern()
            return
        self._last_successful_similarity = None
        try:
            authenticated, similarity = self.authenticate(self._active_user_id, pattern)
        except AuthenticationLockedError as error:
            self.device.notify(f"인증 요청 거부: {error}")
            self.device.send_auth_result(False, 0)
            self._reset_operation()
            return
        except (LookupError, ValueError) as error:
            self.device.notify(f"인증 입력 오류: {error}")
            self.device.send_error("AUTH_PROCESSING")
        else:
            self.device.notify(f"인증 {'성공' if authenticated else '실패'}: 패턴 일치도 {similarity:.3f}")
            if authenticated:
                self._authentication_failures = 0
                self._last_successful_similarity = similarity
                self._mode = "authentication_succeeded"
                self.device.send_auth_result(True)
            else:
                if self.get_authentication_failure_status is None:
                    self._send_auth_failure(count_failure=True)
                    failure_count = self._authentication_failures
                else:
                    failure_count, _ = self._authentication_failure_status(
                        self._active_user_id
                    )
                    self._authentication_failures = failure_count
                    self.device.send_auth_result(
                        False,
                        max(0, MAX_AUTH_ATTEMPTS - failure_count),
                    )
                if failure_count >= MAX_AUTH_ATTEMPTS:
                    self._reset_operation()
                    return
        self._reset_pattern()

    def _finish_calibration(self, pattern: SignalPattern) -> None:
        try:
            record = self.calibrate(pattern) if self.calibrate is not None else None
            if record is None:
                raise ValueError("캘리브레이션 기능이 설정되지 않았습니다.")
        except StorageError:
            raise
        except (RuntimeError, ValueError) as error:
            self.device.notify(f"캘리브레이션 실패: {error}")
            self.device.send_capture_fail("CALIBRATION")
        else:
            self.device.notify(
                "캘리브레이션 저장 완료, 압력 설정 적용 대기: "
                f"기준값 {record.active_threshold:.1f}, 유효 연속 샘플 {record.min_consecutive_samples}개"
            )
            self._send_pressure_config(record, completes_calibration=True)
            self._reset_pattern()
            return
        self._reset_operation()

    def _finish_registration(self, pattern: SignalPattern) -> None:
        request = self._registration_request
        first = self._first_registration_pattern
        try:
            if self.register is None or request is None or first is None:
                raise ValueError("등록 상태가 올바르지 않습니다.")
            registered, similarity = self.register(request, first, pattern)
        except StorageError:
            self._send_capture_cancel()
            raise
        except (RuntimeError, ValueError) as error:
            self.device.notify(f"등록 실패: {error}")
            self.device.send_capture_fail("REGISTRATION")
            self._send_capture_cancel()
            self._reset_operation()
        else:
            if registered:
                self.device.notify(f"등록 완료 (일치도 {similarity:.3f})")
                self.device.send_capture_ok("REGISTRATION")
                self._reset_operation()
                return
            self._second_registration_attempts += 1
            if self._second_registration_attempts < MAX_SECOND_REGISTRATION_ATTEMPTS:
                remaining = MAX_SECOND_REGISTRATION_ATTEMPTS - self._second_registration_attempts
                self.device.notify(
                    f"등록 2차 패턴 불일치 (일치도 {similarity:.3f}, 남은 시도 {remaining}회)"
                )
                self.device.send_registration_retry(remaining)
                self._reset_pattern()
                return
            self.device.notify(
                f"등록 2차 패턴이 3회 불일치해 1차부터 다시 입력합니다. (일치도 {similarity:.3f})"
            )
            self._first_registration_pattern = None
            self._second_registration_attempts = 0
            self._mode = "registration_first"
            self.device.send_registration_restart()
            self._reset_pattern()

    def _handle_boot_ready(self) -> None:
        self.device.notify("Arduino가 준비됐습니다.")
        if self._synchronize_connection:
            self._begin_session_reset()
            return
        self._synchronize_calibration()
        if self._mode in PRESSURE_MONITOR_MODES:
            self.device.notify("Arduino 재부팅으로 압력센서 진단이 종료됐습니다.")
            self._reset_operation()
        elif self._mode in {"authentication", "authentication_succeeded"}:
            self._reset_operation()
        else:
            self._reset_pattern()
            if self._mode == "calibration":
                self.device.request_capture("CALIBRATION")
            elif self._mode == "registration_first":
                self.device.request_capture("REGISTRATION", 1)
            elif self._mode == "registration_second":
                # Do not pair a pattern captured before the Arduino reboot
                # with one captured after it. A reboot returns the firmware
                # to STANDBY, where it accepts a first-pass request but not a
                # second-pass request.
                self._first_registration_pattern = None
                self._second_registration_attempts = 0
                self._mode = "registration_first"
                self.device.request_capture("REGISTRATION", 1)

    def _handle_timeout(self, message: str) -> None:
        if message == "TIMEOUT,ID":
            self.device.notify("아두이노 ID 확인 대기 시간이 초과됐습니다.")
            self._reset_operation()
        elif message == "TIMEOUT,RESULT":
            self.device.notify("아두이노 캡처 결과 대기 시간이 초과됐습니다.")
            self._reset_pattern()

    def _handle_door_status(self, message: str) -> None:
        authentication_completed = self._mode == "authentication_succeeded"
        if message == "DOOR,UNLOCKED":
            status = "문이 열렸습니다."
            if self._last_successful_similarity is not None:
                status += f" (인증 유사도 {self._last_successful_similarity:.3f})"
            self._last_successful_similarity = None
        else:
            status = "문이 잠겼습니다."
            self._last_successful_similarity = None
        self.device.notify(f"[잠금장치] {status}")
        if message == "DOOR,LOCKED" and authentication_completed:
            self._reset_operation()

    def _handle_pattern_abort(self, message: str) -> None:
        try:
            abort = parse_pattern_abort(message)
        except ValueError:
            self.device.notify(f"아두이노 오류: 잘못된 패턴 중단 메시지: {message}")
            return
        if not self._pattern_active:
            self.device.notify(f"아두이노 오류: 활성 패턴 없이 중단 메시지를 받았습니다: {message}")
            return
        if abort.reason == "INACTIVITY":
            self.device.notify(
                "3초간 유효 압력이 없어 현재 패턴을 폐기합니다. "
                f"남은 미입력 기회: {abort.remaining_attempts}회"
            )
        else:
            self.device.notify("압력 패턴 입력 시간이 초과되어 현재 패턴을 폐기합니다.")
        self._reset_pattern()

    def _handle_capture_cancelled(self, message: str) -> None:
        try:
            reason = parse_capture_cancelled(message)
        except ValueError:
            self.device.notify(f"아두이노 오류: 잘못된 캡처 취소 메시지: {message}")
            return
        if reason == "PI":
            if self._pending_session_reset is None:
                self.device.notify(f"아두이노 오류: 요청 없이 세션 초기화 확인을 받았습니다: {message}")
                return
            self._pending_session_reset = None
            self.device.notify("Arduino 잔여 작업을 초기화했습니다.")
            self._begin_device_status_request()
        elif reason == "USER":
            self.device.notify("Arduino에서 사용자가 작업을 취소했습니다.")
        else:
            self.device.notify("3회 연속 미입력으로 Arduino 작업이 취소됐습니다.")
        if reason != "PI":
            self._reset_operation()

    def _handle_capture_rejected(self, message: str) -> None:
        self.device.notify(f"Arduino가 캡처 요청을 거부했습니다: {message}")
        self._reset_operation()

    def _handle_pressure_monitor_started(self, message: str) -> None:
        try:
            parse_pressure_monitor_started(message)
        except ValueError:
            self.device.notify(f"아두이노 오류: 잘못된 진단 시작 메시지: {message}")
            return
        if self._mode == "pressure_monitor_starting":
            self._mode = "pressure_monitor"
            self.device.notify("압력센서 진단을 시작했습니다.")
        elif self._mode != "pressure_monitor_stopping":
            self.device.notify(f"아두이노 오류: 요청 없이 진단 시작 확인을 받았습니다: {message}")

    def _handle_pressure_monitor_sample(self, message: str) -> None:
        if self._mode not in {"pressure_monitor", "pressure_monitor_stopping"}:
            self.device.notify(f"아두이노 오류: 활성 진단 없이 압력 샘플을 받았습니다: {message}")
            return
        try:
            sample = parse_pressure_monitor_sample(message)
        except ProtocolMessageError as error:
            self.device.notify(f"아두이노 오류: 잘못된 진단 샘플 ({error.reason}): {message}")
            return
        self._pressure_monitor_samples.append(sample)

    def _handle_pressure_monitor_stopped(self, message: str) -> None:
        try:
            parse_pressure_monitor_stopped(message)
        except ValueError:
            self.device.notify(f"아두이노 오류: 잘못된 진단 종료 메시지: {message}")
            return
        if self._mode not in PRESSURE_MONITOR_MODES:
            self.device.notify(f"아두이노 오류: 요청 없이 진단 종료 확인을 받았습니다: {message}")
            return
        self._reset_operation()
        self.device.notify("압력센서 진단이 종료됐습니다.")

    def _handle_pressure_monitor_rejected(self, message: str) -> None:
        try:
            reason = parse_pressure_monitor_rejected(message)
        except ValueError:
            self.device.notify(f"아두이노 오류: 잘못된 진단 거부 메시지: {message}")
            return
        if self._mode in PRESSURE_MONITOR_MODES:
            self.device.notify(f"Arduino가 압력센서 진단 요청을 거부했습니다: {reason}")
            self._reset_operation()
        else:
            self.device.notify(f"아두이노 오류: 요청 없이 진단 거부 메시지를 받았습니다: {message}")

    def _handle_pressure_configured(self, message: str) -> None:
        try:
            acknowledged = parse_pressure_configured(message)
        except ValueError:
            self.device.notify(f"아두이노 오류: 잘못된 압력 설정 확인 메시지: {message}")
            return
        pending = self._pending_pressure_config
        if pending is None:
            self.device.notify(f"아두이노 오류: 요청 없이 압력 설정 확인을 받았습니다: {message}")
            return
        if (acknowledged.active_adc, acknowledged.consecutive) != (
            pending.active_adc,
            pending.consecutive,
        ):
            self.device.notify(f"아두이노 오류: 요청값과 다른 압력 설정 확인을 받았습니다: {message}")
            return
        self._pending_pressure_config = None
        self._pressure_config_sync_failed = False
        self.device.notify("Arduino에 유효 압력 기준이 적용됐습니다.")
        if pending.completes_calibration:
            self.device.notify("캘리브레이션 완료: 압력 설정 적용을 확인했습니다.")
            self.device.send_capture_ok("CALIBRATION")
            self._reset_operation()

    def _handle_device_status(self, message: str) -> None:
        try:
            status = parse_status(message)
        except ValueError:
            self.device.notify(f"아두이노 오류: 잘못된 상태 응답: {message}")
            self._begin_session_reset()
            return
        if self._pending_device_status is None:
            self.device.notify(f"아두이노 오류: 요청 없이 상태 응답을 받았습니다: {message}")
            return
        self._last_device_status = status
        if status.mode != "IDLE" or status.door != "LOCKED":
            self.device.notify(
                "Arduino 세션 초기화 후 안전 상태가 아니어서 다시 초기화합니다: "
                f"{status.mode},{status.door}"
            )
            self._begin_session_reset()
            return
        self._pending_device_status = None
        self.device.notify("Arduino 대기 및 잠금 상태를 확인했습니다.")
        self._synchronize_calibration()

    def _dispatch_message(self, message: str) -> None:
        with self._state_lock:
            if self._pending_session_reset is not None and not (
                message == "BOOT,READY" or message.startswith("CAPTURE_CANCELLED")
            ):
                self.device.notify(f"Arduino 세션 초기화 중 메시지를 무시했습니다: {message}")
                return
            if self._pending_device_status is not None and not (
                message == "BOOT,READY" or message.startswith("STATUS")
            ):
                self.device.notify(f"Arduino 상태 확인 중 메시지를 무시했습니다: {message}")
                return
            if message == "BOOT,READY":
                self._handle_boot_ready()
            elif message == "ID_CHECK" or message.startswith("ID_CHECK,"):
                self._handle_id_check(message)
            elif message == "PATTERN_START":
                self._handle_pattern_start()
            elif message == "P" or message.startswith("P,"):
                self._handle_sample(message)
            elif message == "PATTERN_RESET":
                self._handle_pattern_reset()
            elif message.startswith("PATTERN_END"):
                self._handle_pattern_end(message)
            elif message == "PATTERN_ABORT" or message.startswith("PATTERN_ABORT,"):
                self._handle_pattern_abort(message)
            elif message == "CAPTURE_CANCELLED" or message.startswith("CAPTURE_CANCELLED,"):
                self._handle_capture_cancelled(message)
            elif message == "CAPTURE_REJECTED,BUSY":
                self._handle_capture_rejected(message)
            elif message == "M" or message.startswith("M,"):
                self._handle_pressure_monitor_sample(message)
            elif message.startswith("PRESSURE_MONITOR_STARTED"):
                self._handle_pressure_monitor_started(message)
            elif message.startswith("PRESSURE_MONITOR_STOPPED"):
                self._handle_pressure_monitor_stopped(message)
            elif message == "PRESSURE_MONITOR_REJECTED" or message.startswith(
                "PRESSURE_MONITOR_REJECTED,"
            ):
                self._handle_pressure_monitor_rejected(message)
            elif message.startswith("PRESSURE_CONFIGURED"):
                self._handle_pressure_configured(message)
            elif message == "STATUS" or message.startswith("STATUS,"):
                self._handle_device_status(message)
            elif message in {"TIMEOUT,ID", "TIMEOUT,RESULT"}:
                self._handle_timeout(message)
            elif message in {"DOOR,UNLOCKED", "DOOR,LOCKED"}:
                self._handle_door_status(message)
            elif message.startswith("ERROR"):
                self.device.notify(f"아두이노 오류: {message}")
            elif message.startswith(("BOOT,", "STATE,", "DEBUG,", "KEY,")):
                self.device.notify(f"[아두이노 진단] {message}")
            elif message != "PONG":
                self.device.notify(f"알 수 없는 아두이노 메시지: {message}")

    def serve_forever(self) -> int:
        """Process Arduino messages until interrupted or communication fails."""
        self.device.notify("Arduino 인증 서비스를 시작했습니다.")
        self._last_valid_message_at = self._monotonic()
        self._heartbeat_deadline = None
        self._heartbeat_failures = 0
        synchronize_session = self._synchronize_connection
        calibration_synchronized = synchronize_session
        while not self._stop_event.is_set():
            try:
                if synchronize_session and self._pending_session_reset is None:
                    self._begin_session_reset()
                    synchronize_session = False
                if not calibration_synchronized:
                    self._synchronize_calibration()
                    calibration_synchronized = True
                read_timeout = self._session_reset_read_timeout()
                if read_timeout is None:
                    read_timeout = self._device_status_read_timeout()
                if read_timeout is None:
                    read_timeout = self._pressure_config_read_timeout()
                if read_timeout is None:
                    read_timeout = self._heartbeat_read_timeout()
                if read_timeout == 0:
                    if self._pending_session_reset is not None:
                        self._handle_session_reset_timeout()
                    elif self._pending_device_status is not None:
                        self._handle_device_status_timeout()
                    elif self._pending_pressure_config is not None:
                        self._handle_pressure_config_timeout()
                    else:
                        self._handle_heartbeat_timeout()
                    continue
                message = self.device.read_message(read_timeout)
                if self._stop_event.is_set():
                    break
                if message == "PONG":
                    self._handle_pong()
                    continue
                if is_valid_activity_message(message):
                    self._last_valid_message_at = self._monotonic()
                self._dispatch_message(message)
            except KeyboardInterrupt:
                self.device.notify("\nArduino 인증 서비스를 종료합니다.")
                return 0
            except CommunicationTimeoutError:
                try:
                    if self._pending_session_reset is not None:
                        self._handle_session_reset_timeout()
                    elif self._pending_device_status is not None:
                        self._handle_device_status_timeout()
                    elif self._pending_pressure_config is not None:
                        self._handle_pressure_config_timeout()
                    elif self._heartbeat_enabled:
                        self._handle_heartbeat_timeout()
                except CommunicationError as error:
                    self._record_failure(error)
                    return 2
                continue
            except CommunicationError as error:
                self._record_failure(error)
                return 2
            except StorageError as error:
                failure = CommunicationError(f"인증 저장소 처리 실패: {error}")
                self._record_failure(failure)
                return 2
        return 0

    def cancel_active_operation(self) -> bool:
        """Cancel the current device operation and clear all local state."""
        with self._state_lock:
            self.raise_if_failed()
            if self._mode == "idle":
                return False
            if self._mode in PRESSURE_MONITOR_MODES:
                self._send_pressure_monitor_stop()
            else:
                self._send_capture_cancel()
            self._reset_operation()
            self.device.notify("진행 중인 장치 작업을 취소했습니다.")
            return True

    def stop(self) -> None:
        """Cancel device work and ask the background serial loop to finish."""
        with self._state_lock:
            self._stop_event.set()
            if self._failure is None:
                try:
                    if self._mode in PRESSURE_MONITOR_MODES:
                        self._send_pressure_monitor_stop()
                    else:
                        self._send_capture_cancel()
                except CommunicationError:
                    pass
            self._reset_operation()
