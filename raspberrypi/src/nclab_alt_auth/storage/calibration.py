"""JSON-backed calibration persistence."""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING

from .errors import StorageError

if TYPE_CHECKING:
    from ..calibration_data import CalibrationRecord


_BASE_RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "calibrated_at",
        "adc_max",
        "sample_count",
        "baseline_mean",
        "baseline_stddev",
        "baseline_max",
        "safety_margin",
        "active_threshold",
        "min_consecutive_samples",
        "image_size",
    }
)
_SCHEMA_RECORD_FIELDS = {
    1: _BASE_RECORD_FIELDS | {"stddev_multiplier"},
    2: _BASE_RECORD_FIELDS
    | {
        "baseline_median",
        "baseline_mad",
        "baseline_percentile_99",
        "mad_multiplier",
        "min_active_headroom",
    },
}


def _finite_number(record: CalibrationRecord, field: str) -> float:
    value = getattr(record, field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field}는 유한한 숫자여야 합니다.")
    return float(value)


def _validate_record_payload(payload: object) -> CalibrationRecord:
    from ..calibration_data import CalibrationRecord, MIN_CALIBRATION_SAMPLES

    if not isinstance(payload, dict):
        raise ValueError("최상위 JSON 값은 객체여야 합니다.")

    schema_version = payload.get("schema_version")
    if type(schema_version) is not int or schema_version not in _SCHEMA_RECORD_FIELDS:
        raise ValueError("지원하지 않는 캘리브레이션 스키마입니다.")
    expected_fields = _SCHEMA_RECORD_FIELDS[schema_version]
    actual_fields = set(payload)
    if actual_fields != expected_fields:
        missing = sorted(expected_fields - actual_fields)
        unknown = sorted(str(field) for field in actual_fields - expected_fields)
        details = []
        if missing:
            details.append(f"누락={missing}")
        if unknown:
            details.append(f"알 수 없는 필드={unknown}")
        raise ValueError("필드가 올바르지 않습니다: " + ", ".join(details))

    record = CalibrationRecord(**payload)
    if not isinstance(record.calibrated_at, str) or not record.calibrated_at.strip():
        raise ValueError("calibrated_at은 비어 있지 않은 문자열이어야 합니다.")
    try:
        timestamp = record.calibrated_at
        datetime.fromisoformat(timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp)
    except ValueError as error:
        raise ValueError("calibrated_at은 ISO-8601 형식이어야 합니다.") from error
    if type(record.sample_count) is not int or record.sample_count < MIN_CALIBRATION_SAMPLES:
        raise ValueError(f"sample_count는 {MIN_CALIBRATION_SAMPLES} 이상의 정수여야 합니다.")
    if type(record.min_consecutive_samples) is not int or not (
        1 <= record.min_consecutive_samples <= record.sample_count
    ):
        raise ValueError("min_consecutive_samples가 유효한 범위에 있지 않습니다.")
    if type(record.image_size) is not int or record.image_size <= 0:
        raise ValueError("image_size는 0보다 큰 정수여야 합니다.")

    adc_max = _finite_number(record, "adc_max")
    if adc_max <= 0:
        raise ValueError("adc_max는 0보다 커야 합니다.")
    for field in ("baseline_mean", "baseline_max"):
        if not 0 <= _finite_number(record, field) <= adc_max:
            raise ValueError(f"{field}가 ADC 범위에 있지 않습니다.")
    if _finite_number(record, "baseline_stddev") < 0:
        raise ValueError("baseline_stddev는 0 이상이어야 합니다.")
    if _finite_number(record, "safety_margin") < 0:
        raise ValueError("safety_margin은 0 이상이어야 합니다.")
    active_threshold = _finite_number(record, "active_threshold")
    if not 0 <= active_threshold < adc_max:
        raise ValueError("active_threshold가 ADC 범위에 있지 않습니다.")

    if schema_version == 1:
        if _finite_number(record, "stddev_multiplier") < 0:
            raise ValueError("stddev_multiplier는 0 이상이어야 합니다.")
    else:
        for field in ("baseline_median", "baseline_percentile_99"):
            if not 0 <= _finite_number(record, field) <= adc_max:
                raise ValueError(f"{field}가 ADC 범위에 있지 않습니다.")
        if _finite_number(record, "baseline_mad") < 0:
            raise ValueError("baseline_mad는 0 이상이어야 합니다.")
        if _finite_number(record, "mad_multiplier") < 0:
            raise ValueError("mad_multiplier는 0 이상이어야 합니다.")
        min_active_headroom = _finite_number(record, "min_active_headroom")
        if not 0 <= min_active_headroom < adc_max:
            raise ValueError("min_active_headroom이 ADC 범위에 있지 않습니다.")
        if adc_max - active_threshold < min_active_headroom:
            raise ValueError("활성 임계값이 필요한 ADC 여유 범위를 남기지 않습니다.")

    return record


class CalibrationRepository:
    """Store one active device calibration in ``calibration.json``."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "calibration.json"

    def get(self) -> CalibrationRecord | None:
        try:
            if not self.path.exists():
                return None
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return _validate_record_payload(payload)
        except (OSError, UnicodeError, TypeError, ValueError) as error:
            raise StorageError(f"캘리브레이션 데이터를 읽을 수 없습니다: {error}") from error

    def save(self, record: CalibrationRecord) -> None:
        try:
            payload = {key: value for key, value in asdict(record).items() if value is not None}
            _validate_record_payload(payload)
            document = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise StorageError(f"저장할 캘리브레이션 데이터가 올바르지 않습니다: {error}") from error

        temporary_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.path.parent, delete=False
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(document)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            temporary_path.replace(self.path)
        except OSError as error:
            raise StorageError(f"캘리브레이션 데이터 저장 실패: {error}") from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
