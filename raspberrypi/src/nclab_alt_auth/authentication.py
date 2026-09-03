"""사용자 등록, 압력 패턴 비교와 인증 판정을 처리하는 핵심 서비스."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .calibration_data import (
    CalibrationRecord,
    validate_calibration_sampling_window,
)
from .embedding_utils import (
    average_embeddings,
    cosine_similarity,
    normalize_embedding,
)
from .enrollment_data import AuthenticationLockout, EnrollmentRecord, User
from .identity import validate_user_id
from .inference import EmbeddingModel
from .preprocessing import (
    FSRImagePreprocessor,
    PatternInput,
    SignalPattern,
    parse_signal_pattern,
)
from .storage.calibration import CalibrationRepository
from .storage.enrollment import EnrollmentRepository


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AuthenticationLockedError(RuntimeError):
    """연속 인증 실패로 사용자의 인증 시도가 잠긴 경우 발생한다."""

    def __init__(self, user_id: str, remaining_seconds: int) -> None:
        self.user_id = user_id
        self.remaining_seconds = remaining_seconds
        super().__init__(f"인증이 잠겨 있습니다. {remaining_seconds}초 후에 다시 시도하세요.")


@dataclass(frozen=True)
class PatternAuthService:
    """센서 패턴을 전처리·임베딩하고 등록 정보와 비교하는 인증 서비스."""

    enrollments: EnrollmentRepository
    model: EmbeddingModel
    calibrations: CalibrationRepository | None = None
    max_attempts: int = 3
    clock: Callable[[], datetime] = _utc_now

    @property
    def similarity_threshold(self) -> float:
        """현재 모델 설정에서 등록과 인증에 공통으로 사용하는 유사도 임계값을 반환한다."""
        return self.model.similarity_threshold

    @staticmethod
    def _pattern(pattern: PatternInput) -> SignalPattern:
        return pattern if isinstance(pattern, SignalPattern) else parse_signal_pattern(pattern)

    def _embed(self, raw_pattern: PatternInput) -> list[float]:
        """원시 압력 패턴을 장치 보정값으로 전처리하고 AI 임베딩으로 변환한다."""
        calibration = self.calibrations.get() if self.calibrations is not None else None
        preprocessor = (
            calibration.preprocessor()
            if calibration is not None
            else FSRImagePreprocessor()
        )
        expected_size = (preprocessor.image_size, preprocessor.image_size)
        if self.model.image_size != expected_size:
            raise ValueError(
                "전처리 이미지 크기와 모델 입력 크기가 일치하지 않습니다: "
                f"{expected_size} != {self.model.image_size}"
            )
        pattern = self._pattern(raw_pattern)
        image = preprocessor.transform_pattern(pattern)
        return self.model.infer(image)

    def calibrate(self, idle_pattern: PatternInput) -> CalibrationRecord:
        """무입력 상태의 센서값을 저장해 이후 등록·인증 전처리에 공통 적용한다."""
        if self.calibrations is None:
            raise RuntimeError("캘리브레이션 저장소가 설정되지 않았습니다.")
        pattern = self._pattern(idle_pattern)
        validate_calibration_sampling_window(pattern)
        record = CalibrationRecord.from_idle_samples(list(pattern.values))
        self.calibrations.save(record)
        return record

    def get_calibration(self) -> CalibrationRecord | None:
        """현재 Raspberry Pi에 저장된 센서 캘리브레이션 정보를 반환한다."""
        return self.calibrations.get() if self.calibrations is not None else None

    def register(
        self,
        user: User,
        first_pattern: PatternInput,
        second_pattern: PatternInput,
    ) -> tuple[bool, float]:
        """두 번 입력한 패턴이 충분히 유사하면 평균 임베딩을 사용자 정보로 등록한다."""
        validate_user_id(user.user_id)
        first = self._embed(first_pattern)
        second = self._embed(second_pattern)
        similarity = cosine_similarity(first, second)
        if similarity < self.similarity_threshold:
            return False, similarity

        # 한 번의 입력에 과도하게 의존하지 않도록 두 등록 임베딩의 평균을 기준 패턴으로 저장한다.
        self.enrollments.save(
            EnrollmentRecord(
                user_id=user.user_id,
                name=user.name,
                model_version=self.model.version,
                embedding=normalize_embedding(average_embeddings([first, second])),
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        self.enrollments.clear_auth_failures(user.user_id)
        return True, similarity

    def is_enrolled(self, user_id: str) -> bool:
        """현재 모델 버전으로 사용할 수 있는 등록 사용자인지 확인한다."""
        validate_user_id(user_id)
        enrolled = self.enrollments.get(user_id)
        return enrolled is not None and enrolled.model_version == self.model.version

    def update_user(self, old_user_id: str, new_user_id: str, name: str) -> bool:
        """등록된 압력 패턴은 유지하면서 사용자 ID와 이름을 수정한다."""
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return self.enrollments.update_user(
            old_user_id,
            new_user_id,
            name,
            updated_at=now.astimezone(timezone.utc).isoformat(),
        )

    def delete_user(self, user_id: str) -> bool:
        """사용자 등록 정보와 해당 사용자의 인증 실패 상태를 함께 삭제한다."""
        return self.enrollments.delete(user_id)

    def get_auth_lockout(self, user_id: str) -> AuthenticationLockout | None:
        """사용자의 현재 인증 실패·잠금 상태를 반환한다."""
        return self.enrollments.get_auth_lockout(user_id)

    def auth_lockout_remaining_seconds(self, user_id: str) -> int:
        """현재 인증 잠금이 유지되는 남은 시간을 초 단위로 반환한다."""
        lockout = self.get_auth_lockout(user_id)
        if lockout is None or lockout.locked_until is None:
            return 0
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        seconds = (lockout.locked_until - now.astimezone(timezone.utc)).total_seconds()
        return max(0, math.ceil(seconds))

    def authentication_failure_status(self, user_id: str) -> tuple[int, int]:
        """저장된 인증 실패 횟수와 잠금 잔여 시간을 반환한다."""
        lockout = self.get_auth_lockout(user_id)
        if lockout is None:
            return 0, 0
        if lockout.locked_until is None:
            return lockout.failure_count, 0
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        seconds = (lockout.locked_until - now.astimezone(timezone.utc)).total_seconds()
        return lockout.failure_count, max(0, math.ceil(seconds))

    def require_authentication_allowed(self, user_id: str) -> None:
        """잠금 시간이 남은 사용자의 추가 인증 시도를 차단한다."""
        validate_user_id(user_id)
        remaining_seconds = self.auth_lockout_remaining_seconds(user_id)
        if remaining_seconds > 0:
            raise AuthenticationLockedError(user_id, remaining_seconds)

    def authenticate(self, user_id: str, pattern: PatternInput) -> tuple[bool, float]:
        """새 입력 임베딩과 등록 임베딩의 코사인 유사도로 인증 성공 여부를 판정한다."""
        validate_user_id(user_id)
        self.require_authentication_allowed(user_id)
        enrolled = self.enrollments.get(user_id)
        if enrolled is None:
            raise LookupError("등록 패턴 데이터가 없습니다. 사용자를 다시 등록하세요.")
        if enrolled.model_version != self.model.version:
            raise LookupError("등록 패턴의 모델 버전이 다릅니다. 사용자를 다시 등록하세요.")

        candidate = self._embed(pattern)
        # 같은 사용자도 입력할 때마다 압력값이 조금씩 달라지므로 원시값 대신 특징 벡터 유사도를 비교한다.
        similarity = cosine_similarity(enrolled.embedding, candidate)
        authenticated = similarity >= self.similarity_threshold
        if authenticated:
            self.enrollments.clear_auth_failures(user_id)
        else:
            self.enrollments.record_auth_failure(user_id)
        return authenticated, similarity
