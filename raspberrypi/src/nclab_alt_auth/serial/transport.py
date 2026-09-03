"""Serial port transport for newline-delimited Arduino messages."""

from __future__ import annotations

import math
import time
from threading import Lock
from typing import Protocol

from .protocol import MAX_MESSAGE_BYTES


class CommunicationError(RuntimeError):
    """Raised when the Arduino serial connection fails."""


class CommunicationTimeoutError(CommunicationError):
    """Raised when no complete Arduino message arrives before the deadline."""


class SerialConnection(Protocol):
    """The pyserial operations used by :class:`ArduinoSerialTransport`."""

    def write(self, data: bytes) -> object: ...

    def flush(self) -> None: ...

    def readline(self) -> bytes: ...

    def close(self) -> None: ...


class ArduinoSerialTransport:
    """Read and write newline-delimited ASCII messages over a serial port."""

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        timeout: float = 10.0,
        serial_connection: SerialConnection | None = None,
    ) -> None:
        if not isinstance(baudrate, int) or isinstance(baudrate, bool) or baudrate <= 0:
            raise ValueError("시리얼 통신 속도는 양의 정수여야 합니다.")
        self.timeout = self._validate_timeout(timeout)
        self._write_lock = Lock()
        self._read_buffer = bytearray()
        if serial_connection is not None:
            self._serial = serial_connection
            return
        self._serial = self._open_serial(port, baudrate, timeout)

    @staticmethod
    def _validate_timeout(timeout: float) -> float:
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
            raise ValueError("시리얼 타임아웃은 유한한 양수여야 합니다.")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("시리얼 타임아웃은 유한한 양수여야 합니다.")
        return float(timeout)

    @staticmethod
    def _open_serial(port: str, baudrate: int, timeout: float) -> SerialConnection:
        try:
            import serial  # type: ignore[import-not-found]
        except ImportError as error:
            raise CommunicationError(
                "pyserial 설치를 확인하세요: python -m pip install -e ."
            ) from error
        try:
            return serial.Serial(port=port, baudrate=baudrate, timeout=min(timeout, 1.0))
        except serial.SerialException as error:
            raise CommunicationError(f"아두이노 시리얼 포트를 열 수 없습니다: {error}") from error

    def write_message(self, message: str) -> None:
        """Write one ASCII message followed by exactly one line feed."""
        try:
            encoded = message.encode("ascii")
            if len(encoded) > MAX_MESSAGE_BYTES or b"\r" in encoded or b"\n" in encoded:
                raise ValueError(
                    f"시리얼 메시지는 줄바꿈 없는 ASCII {MAX_MESSAGE_BYTES}바이트 이하여야 합니다."
                )
            with self._write_lock:
                self._serial.write(encoded + b"\n")
                self._serial.flush()
        except (OSError, ValueError) as error:
            raise CommunicationError(f"아두이노 명령 전송 실패: {error}") from error

    def read_message(self, timeout: float | None = None) -> str:
        """Read one non-empty ASCII line emitted by the Arduino."""
        effective_timeout = self.timeout if timeout is None else self._validate_timeout(timeout)
        deadline = time.monotonic() + effective_timeout
        while True:
            newline_index = self._read_buffer.find(b"\n")
            if newline_index >= 0:
                payload = bytes(self._read_buffer[:newline_index])
                del self._read_buffer[: newline_index + 1]
                if payload.endswith(b"\r"):
                    payload = payload[:-1]
                if len(payload) > MAX_MESSAGE_BYTES:
                    raise CommunicationError(
                        f"아두이노 메시지가 {MAX_MESSAGE_BYTES}바이트 제한을 초과했습니다."
                    )
                try:
                    line = payload.decode("ascii")
                except UnicodeDecodeError as error:
                    raise CommunicationError("아두이노가 ASCII가 아닌 데이터를 전송했습니다.") from error
                if line:
                    return line
                continue
            valid_pending_cr = (
                len(self._read_buffer) == MAX_MESSAGE_BYTES + 1
                and self._read_buffer.endswith(b"\r")
            )
            if len(self._read_buffer) > MAX_MESSAGE_BYTES and not valid_pending_cr:
                raise CommunicationError(
                    f"아두이노 메시지가 {MAX_MESSAGE_BYTES}바이트 제한을 초과했습니다."
                )
            if time.monotonic() >= deadline:
                break
            try:
                raw = self._serial.readline()
            except OSError as error:
                raise CommunicationError(f"아두이노 데이터 수신 실패: {error}") from error
            if not raw:
                continue
            self._read_buffer.extend(raw)
        raise CommunicationTimeoutError(f"{effective_timeout:g}초 안에 아두이노 메시지를 받지 못했습니다.")

    def close(self) -> None:
        self._serial.close()
