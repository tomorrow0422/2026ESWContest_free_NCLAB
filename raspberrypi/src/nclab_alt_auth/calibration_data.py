"""Persistent baseline-noise calibration for the FSR sensor."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import fmean, median, pstdev

from .preprocessing import FSRImagePreprocessor, SignalPattern


MIN_CALIBRATION_SAMPLES = 400
MIN_CALIBRATION_DURATION_US = 1_900_000
MAX_CALIBRATION_DURATION_US = 2_500_000
MIN_CALIBRATION_INTERVAL_US = 2_500
MAX_CALIBRATION_INTERVAL_US = 15_000
DEFAULT_MAD_MULTIPLIER = 6.0
DEFAULT_MIN_ACTIVE_HEADROOM = 200.0


def _percentile_nearest_rank(samples: list[float], percentile: float) -> float:
    """Return a deterministic nearest-rank percentile for a non-empty sample."""
    ordered = sorted(samples)
    rank = math.ceil(percentile * len(ordered))
    return ordered[max(0, min(rank - 1, len(ordered) - 1))]


def _has_consecutive_samples_above(
    samples: list[float], threshold: float, required_count: int
) -> bool:
    consecutive_count = 0
    for sample in samples:
        if sample > threshold:
            consecutive_count += 1
            if consecutive_count >= required_count:
                return True
        else:
            consecutive_count = 0
    return False


def validate_calibration_sampling_window(pattern: SignalPattern) -> None:
    """Require a timestamped, approximately 200 Hz calibration capture."""
    sample_count = len(pattern.values)
    if sample_count < MIN_CALIBRATION_SAMPLES:
        raise ValueError(
            f"캨리브레이션에는 기본 상태 샘플이 최소 {MIN_CALIBRATION_SAMPLES}개 필요합니다."
        )

    elapsed_us = pattern.elapsed_us
    if elapsed_us is None:
        raise ValueError("캨리브레이션에는 Arduino 경과 시간이 필요합니다.")
    if len(elapsed_us) != sample_count:
        raise ValueError("캨리브레이션 타임스탬프와 ADC 샘플 개수가 일치해야 합니다.")
    if not all(type(timestamp) is int and timestamp >= 0 for timestamp in elapsed_us):
        raise ValueError("캨리브레이션 타임스탬프는 0 이상의 정수여야 합니다.")

    intervals = [right - left for left, right in zip(elapsed_us, elapsed_us[1:])]
    if any(
        interval < MIN_CALIBRATION_INTERVAL_US
        or interval > MAX_CALIBRATION_INTERVAL_US
        for interval in intervals
    ):
        raise ValueError(
            "캨리브레이션 샘플 간격은 "
            f"{MIN_CALIBRATION_INTERVAL_US}~{MAX_CALIBRATION_INTERVAL_US}µs 범위여야 합니다."
        )

    duration_us = elapsed_us[-1] - elapsed_us[0]
    if not MIN_CALIBRATION_DURATION_US <= duration_us <= MAX_CALIBRATION_DURATION_US:
        raise ValueError(
            "캨리브레이션 측정 시간은 "
            f"{MIN_CALIBRATION_DURATION_US}~{MAX_CALIBRATION_DURATION_US}µs 범위여야 합니다."
        )


@dataclass(frozen=True)
class CalibrationRecord:
    """Device-level thresholds derived from an idle FSR capture."""

    schema_version: int
    calibrated_at: str
    adc_max: float
    sample_count: int
    baseline_mean: float
    baseline_stddev: float
    baseline_max: float
    safety_margin: float
    active_threshold: float
    min_consecutive_samples: int
    image_size: int
    baseline_median: float | None = None
    baseline_mad: float | None = None
    baseline_percentile_99: float | None = None
    mad_multiplier: float | None = None
    min_active_headroom: float | None = None
    # schema v1 JSON compatibility; schema v2 no longer uses this value.
    stddev_multiplier: float | None = None

    @classmethod
    def from_idle_samples(
        cls,
        samples: list[float],
        *,
        adc_max: float = 16383.0,
        safety_margin: float = 20.0,
        mad_multiplier: float = DEFAULT_MAD_MULTIPLIER,
        min_active_headroom: float = DEFAULT_MIN_ACTIVE_HEADROOM,
        min_consecutive_samples: int = 4,
        image_size: int = 64,
    ) -> "CalibrationRecord":
        if not math.isfinite(adc_max) or adc_max <= 0:
            raise ValueError("ADC 최대값은 0보다 큰 유한한 값이어야 합니다.")
        if not math.isfinite(safety_margin) or safety_margin < 0:
            raise ValueError("안전 여유값은 0 이상의 유한한 값이어야 합니다.")
        if not math.isfinite(mad_multiplier) or mad_multiplier < 0:
            raise ValueError("MAD 배수는 0 이상의 유한한 값이어야 합니다.")
        if (
            not math.isfinite(min_active_headroom)
            or min_active_headroom < 0
            or min_active_headroom >= adc_max
        ):
            raise ValueError("ADC 여유 범위는 0 이상이고 ADC 최대값보다 작아야 합니다.")
        if min_consecutive_samples < 1:
            raise ValueError("유효 연속 샘플 개수는 1 이상이어야 합니다.")

        if len(samples) < MIN_CALIBRATION_SAMPLES:
            raise ValueError(
                f"캘리브레이션에는 기본 상태 샘플이 최소 {MIN_CALIBRATION_SAMPLES}개 필요합니다."
            )
        if not all(math.isfinite(sample) and 0 <= sample <= adc_max for sample in samples):
            raise ValueError("캘리브레이션 샘플은 ADC 범위 안의 유한한 값이어야 합니다.")
        mean = fmean(samples)
        deviation = pstdev(samples)
        maximum = max(samples)
        baseline_median = median(samples)
        baseline_mad = median(abs(sample - baseline_median) for sample in samples)
        percentile_99 = _percentile_nearest_rank(samples, 0.99)

        robust_idle_ceiling = baseline_median + max(
            safety_margin,
            mad_multiplier * baseline_mad,
        )
        if _has_consecutive_samples_above(
            samples,
            robust_idle_ceiling,
            min_consecutive_samples,
        ):
            raise ValueError(
                "무압력 캘리브레이션 중 연속된 압력 샘플이 감지됐습니다. "
                "센서를 누르지 않았는지 확인하세요."
            )

        threshold = max(
            percentile_99 + safety_margin,
            baseline_median + mad_multiplier * baseline_mad,
        )
        if threshold >= adc_max:
            raise ValueError(
                "계산된 활성 임계값이 ADC 범위를 벗어납니다. "
                "센서 포화와 회로 설정을 확인하세요."
            )
        if adc_max - threshold < min_active_headroom:
            raise ValueError(
                "유효한 압력 패턴을 측정할 ADC 여유 범위가 부족합니다. "
                "센서 포화와 회로 설정을 확인하세요."
            )

        return cls(
            schema_version=2,
            calibrated_at=datetime.now(timezone.utc).isoformat(),
            adc_max=adc_max,
            sample_count=len(samples),
            baseline_mean=mean,
            baseline_stddev=deviation,
            baseline_max=maximum,
            safety_margin=safety_margin,
            active_threshold=threshold,
            min_consecutive_samples=min_consecutive_samples,
            image_size=image_size,
            baseline_median=baseline_median,
            baseline_mad=baseline_mad,
            baseline_percentile_99=percentile_99,
            mad_multiplier=mad_multiplier,
            min_active_headroom=min_active_headroom,
        )

    def preprocessor(self) -> FSRImagePreprocessor:
        return FSRImagePreprocessor(
            image_size=self.image_size,
            adc_max=self.adc_max,
            active_threshold=self.active_threshold,
            min_consecutive_samples=self.min_consecutive_samples,
        )
