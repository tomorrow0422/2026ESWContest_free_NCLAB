"""Arduino serial protocol, transport, and session boundaries."""

from .protocol import ArduinoProtocolClient
from .session import ArduinoPatternSession, RegistrationRequest
from .transport import (
    ArduinoSerialTransport,
    CommunicationError,
    CommunicationTimeoutError,
)

__all__ = [
    "ArduinoPatternSession",
    "ArduinoProtocolClient",
    "ArduinoSerialTransport",
    "CommunicationError",
    "CommunicationTimeoutError",
    "RegistrationRequest",
]
