"""Canonical identity-field validation shared by every application boundary."""

from __future__ import annotations

import re


USER_ID_PATTERN = re.compile(r"[0-9]{1,10}\Z", re.ASCII)
USER_ID_ERROR = "사용자 ID는 ASCII 숫자 1~10자리여야 합니다."


def validate_user_id(user_id: str) -> str:
    """Return an unchanged canonical user ID or reject it."""
    if not isinstance(user_id, str) or USER_ID_PATTERN.fullmatch(user_id) is None:
        raise ValueError(USER_ID_ERROR)
    return user_id
