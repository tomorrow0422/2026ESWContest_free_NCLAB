"""Arduino에서 수신한 메시지가 정상적인 운영 프로토콜인지 검증한다."""

from .protocol import (
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


def is_valid_activity_message(message: str) -> bool:
    """완성된 메시지가 운영 프로토콜 형식을 만족하는지 확인한다."""
    try:
        if message in {
            "BOOT,READY",
            "PATTERN_START",
            "PATTERN_RESET",
            "CAPTURE_REJECTED,BUSY",
            "TIMEOUT,ID",
            "TIMEOUT,RESULT",
            "DOOR,UNLOCKED",
            "DOOR,LOCKED",
        }:
            return True
        if message == "ID_CHECK" or message.startswith("ID_CHECK,"):
            parse_id_check(message)
        elif message == "P" or message.startswith("P,"):
            parse_pressure_sample(message)
        elif message.startswith("PATTERN_END"):
            parse_pattern_end(message)
        elif message == "PATTERN_ABORT" or message.startswith("PATTERN_ABORT,"):
            parse_pattern_abort(message)
        elif message == "CAPTURE_CANCELLED" or message.startswith("CAPTURE_CANCELLED,"):
            parse_capture_cancelled(message)
        elif message == "M" or message.startswith("M,"):
            parse_pressure_monitor_sample(message)
        elif message.startswith("PRESSURE_MONITOR_STARTED"):
            parse_pressure_monitor_started(message)
        elif message.startswith("PRESSURE_MONITOR_STOPPED"):
            parse_pressure_monitor_stopped(message)
        elif message == "PRESSURE_MONITOR_REJECTED" or message.startswith(
            "PRESSURE_MONITOR_REJECTED,"
        ):
            parse_pressure_monitor_rejected(message)
        elif message.startswith("PRESSURE_CONFIGURED"):
            parse_pressure_configured(message)
        elif message == "STATUS" or message.startswith("STATUS,"):
            parse_status(message)
        elif message.startswith("ERROR,"):
            return True
        else:
            return False
    except (ProtocolMessageError, ValueError):
        return False
    return True
