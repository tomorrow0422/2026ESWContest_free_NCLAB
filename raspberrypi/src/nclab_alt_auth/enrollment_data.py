from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class User:
    user_id: str
    name: str


@dataclass(frozen=True)
class EnrollmentRecord:
    user_id: str
    name: str
    model_version: str
    embedding: list[float]
    updated_at: str


@dataclass(frozen=True)
class AuthenticationLockout:
    user_id: str
    failure_count: int
    locked_until: datetime | None
