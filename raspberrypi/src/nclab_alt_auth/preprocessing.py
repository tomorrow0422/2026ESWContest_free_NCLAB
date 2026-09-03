"""시간 순서로 수집한 FSR 압력 신호를 AI 모델 입력용 흑백 이미지로 변환한다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Union

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure


ADC_MIN = 0
ADC_MAX = 16383
RASTER_DPI = 100


class PatternFormatError(ValueError):
    """센서 샘플을 유효한 압력 패턴으로 해석할 수 없을 때 발생한다."""


@dataclass(frozen=True)
class SignalPattern:
    """압력값과 선택적인 실제 경과시간을 함께 보관하는 한 번의 입력 패턴."""

    values: tuple[float, ...]
    elapsed_us: tuple[int, ...] | None = None


PatternInput = Union[str, SignalPattern]


def parse_signal_pattern(raw: str) -> SignalPattern:
    """명시적 타임스탬프가 없는 쉼표 구분 ADC 샘플 문자열을 패턴으로 변환한다."""
    values = [value.strip() for value in raw.split(",") if value.strip()]
    if not all(value.isascii() and value.isdecimal() for value in values):
        raise PatternFormatError(
            "센서 샘플은 쉼표로 구분한 ASCII 정수여야 합니다. "
            "예: 510,525,610,720,540"
        )
    samples = [int(value) for value in values]
    if len(samples) < 5:
        raise PatternFormatError("아날로그 패턴은 최소 5개의 시간순 센서 샘플이 필요합니다.")
    if not all(ADC_MIN <= sample <= ADC_MAX for sample in samples):
        raise PatternFormatError(f"센서 샘플은 14비트 ADC 범위 {ADC_MIN}~{ADC_MAX} 안이어야 합니다.")
    return SignalPattern(tuple(float(sample) for sample in samples))


def parse_signal_samples(raw: str) -> list[float]:
    """타임스탬프를 사용하지 않는 호출부를 위해 ADC 값 목록만 반환한다."""
    return list(parse_signal_pattern(raw).values)


def _active_runs(
    samples: Sequence[float],
    threshold: float,
    consecutive: int,
) -> list[tuple[int, int]]:
    """연속 샘플 기준으로 실제 압력이 가해진 안정 구간을 찾는다."""
    runs: list[tuple[int, int]] = []
    active_start: int | None = None
    high_start: int | None = None
    high_count = 0
    low_count = 0

    for index, sample in enumerate(samples):
        if sample > threshold:
            low_count = 0
            if active_start is None:
                if high_start is None:
                    high_start = index
                high_count += 1
                if high_count >= consecutive:
                    active_start = high_start
                    high_start = None
                    high_count = 0
        else:
            high_start = None
            high_count = 0
            if active_start is not None:
                low_count += 1
                if low_count >= consecutive:
                    runs.append((active_start, index - consecutive))
                    active_start = None
                    low_count = 0
    if active_start is not None:
        runs.append((active_start, len(samples) - 1))
    return runs


@dataclass(frozen=True)
class FSRImagePreprocessor:
    """학습 단계와 동일한 규칙으로 압력 구간을 정리하고 선 그래프 이미지로 변환한다."""

    image_size: int = 64
    adc_max: float = 16383.0
    active_threshold: float = 80.0
    min_consecutive_samples: int = 4
    line_width: float = 2.0

    def _trim_bounds(self, samples: Sequence[float]) -> tuple[int, int]:
        """무입력 구간을 제외하고 실제 압력이 시작·종료된 범위를 계산한다."""
        if len(samples) < 5:
            raise PatternFormatError("아날로그 패턴은 최소 5개의 시간순 센서 샘플이 필요합니다.")
        runs = _active_runs(samples, self.active_threshold, self.min_consecutive_samples)
        if not runs:
            raise PatternFormatError("유효 압력 구간이 없습니다. 임계값 이상의 압력을 다시 입력하세요.")
        return runs[0][0], runs[-1][1] + 1

    def trim(self, samples: Sequence[float]) -> list[float]:
        """압력이 실제로 입력된 구간만 잘라 반환한다."""
        start, end = self._trim_bounds(samples)
        return list(samples[start:end])

    def transform(self, samples: Sequence[float]) -> list[list[float]]:
        """모든 샘플 간격이 동일하다고 가정해 모델 입력 이미지로 변환한다."""
        return self.transform_pattern(SignalPattern(tuple(samples)))

    def transform_pattern(self, pattern: SignalPattern) -> list[list[float]]:
        """학습 이미지 생성 규칙과 동일하게 압력-시간 궤적을 64×64 흑백 이미지로 만든다."""
        samples = pattern.values
        start, end = self._trim_bounds(samples)
        trimmed = samples[start:end]

        # 타임스탬프가 있으면 실제 입력 시간 간격을 보존하고, 없으면 균등 간격으로 배치한다.
        if pattern.elapsed_us is None:
            x_positions = [
                index / (len(trimmed) - 1) if len(trimmed) > 1 else 0.0
                for index in range(len(trimmed))
            ]
        else:
            elapsed_us = pattern.elapsed_us
            if len(elapsed_us) != len(samples):
                raise PatternFormatError("타임스탬프와 ADC 샘플 개수가 일치해야 합니다.")
            if any(timestamp < 0 for timestamp in elapsed_us) or any(
                right <= left for left, right in zip(elapsed_us, elapsed_us[1:])
            ):
                raise PatternFormatError("타임스탬프는 0 이상이며 이전 값보다 커야 합니다.")
            trimmed_elapsed_us = elapsed_us[start:end]
            duration_us = trimmed_elapsed_us[-1] - trimmed_elapsed_us[0]
            if duration_us <= 0:
                raise PatternFormatError("압력 패턴의 실제 측정 시간이 필요합니다.")
            x_positions = [
                (timestamp - trimmed_elapsed_us[0]) / duration_us
                for timestamp in trimmed_elapsed_us
            ]

        # 장치마다 ADC 절대값이 달라도 동일한 입력 범위로 모델에 전달되도록 0~1로 정규화한다.
        y_positions = [
            min(max(sample / self.adc_max, 0.0), 1.0)
            for sample in trimmed
        ]

        figure = Figure(
            figsize=(self.image_size / RASTER_DPI, self.image_size / RASTER_DPI),
            dpi=RASTER_DPI,
            facecolor="white",
        )
        canvas = FigureCanvasAgg(figure)
        axes = figure.subplots()
        axes.set_facecolor("white")
        axes.plot(
            x_positions,
            y_positions,
            linewidth=self.line_width * 72 / RASTER_DPI,
            color="black",
        )
        axes.set_xlim(0, 1)
        axes.set_ylim(0, 1)
        axes.axis("off")
        figure.subplots_adjust(left=0, right=1, top=1, bottom=0)
        canvas.draw()

        rgba = np.asarray(canvas.buffer_rgba(), dtype=np.uint8)
        grayscale = rgba[:, :, 0].astype(np.float32) / 255.0
        return grayscale.tolist()
