"""Immutable state values used by the serial session coordinator."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RegistrationRequest:
    user_id: str
    name: str


@dataclass(frozen=True)
class PendingPressureConfiguration:
    active_adc: int
    consecutive: int
    completes_calibration: bool
    attempts: int = 0
    deadline: float = 0.0


@dataclass(frozen=True)
class PendingSessionReset:
    attempts: int = 0
    deadline: float = 0.0


@dataclass(frozen=True)
class PendingDeviceStatus:
    attempts: int = 0
    deadline: float = 0.0
