"""전처리된 FSR 압력 패턴 이미지를 ONNX 모델로 임베딩 벡터로 변환한다."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np
import onnxruntime as ort


class EmbeddingModel(Protocol):
    """인증 서비스가 사용하는 임베딩 모델의 공통 인터페이스."""

    @property
    def version(self) -> str: ...

    @property
    def embedding_dimension(self) -> int: ...

    @property
    def image_size(self) -> tuple[int, int]: ...

    @property
    def similarity_threshold(self) -> float: ...

    def infer(self, image: Sequence[Sequence[float]]) -> list[float]: ...


class OnnxEmbeddingModel:
    """학습 완료 ONNX 모델을 로드하고 정규화된 압력 패턴 임베딩을 생성한다."""

    def __init__(
        self,
        model_path: Path,
        config_path: Path,
        *,
        operating_point: str = "balanced",
    ) -> None:
        self.model_path = Path(model_path)
        self.config_path = Path(config_path)
        if not self.model_path.is_file():
            raise FileNotFoundError(f"ONNX 모델 파일을 찾을 수 없습니다: {self.model_path}")

        config = self._load_config(self.config_path)
        try:
            input_config = config["input"]
            output_config = config["output"]
            verification_config = config["verification"]
            thresholds = verification_config["thresholds"]
            self._version = config["model_version"]
            self._image_height = int(input_config["height"])
            self._image_width = int(input_config["width"])
            self._embedding_dimension = int(output_config["embedding_dim"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("모델 설정 구조가 올바르지 않습니다.") from error

        if config.get("schema_version") != 1:
            raise ValueError("지원하지 않는 모델 설정 schema_version입니다.")
        if not isinstance(self._version, str) or not self._version.strip():
            raise ValueError("모델 버전은 비어 있지 않은 문자열이어야 합니다.")
        if (
            input_config.get("channels") != 1
            or input_config.get("color_mode") != "L"
            or input_config.get("dtype") != "float32"
            or input_config.get("pixel_min") != 0.0
            or input_config.get("pixel_max") != 1.0
            or self._image_height <= 0
            or self._image_width <= 0
        ):
            raise ValueError("지원하지 않는 모델 입력 설정입니다.")
        if (
            self._embedding_dimension <= 0
            or output_config.get("normalization") != "l2"
        ):
            raise ValueError("지원하지 않는 모델 출력 설정입니다.")
        if (
            verification_config.get("accept_operator") != ">="
            or verification_config.get("metric") != "cosine_similarity"
            or verification_config.get("enrollment_images") != 2
        ):
            raise ValueError("지원하지 않는 인증 설정입니다.")
        if not isinstance(thresholds, dict) or operating_point not in thresholds:
            raise ValueError(f"알 수 없는 모델 operating point입니다: {operating_point}")

        threshold = thresholds[operating_point]
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not math.isfinite(threshold)
        ):
            raise ValueError(f"인증 임계값이 올바르지 않습니다: {operating_point}")
        self._operating_point = operating_point
        self._similarity_threshold = float(threshold)

        # 시연 장치는 외부 서버가 아니라 Raspberry Pi CPU에서 직접 추론한다.
        try:
            self._session = ort.InferenceSession(
                str(self.model_path),
                providers=["CPUExecutionProvider"],
            )
        except Exception as error:
            raise ValueError(f"ONNX 모델을 초기화할 수 없습니다: {self.model_path}") from error
        self._validate_model_contract()

    @staticmethod
    def _load_config(path: Path) -> dict[str, object]:
        """모델의 입출력 규격과 인증 임계값을 정의한 설정 파일을 읽는다."""
        if not path.is_file():
            raise FileNotFoundError(f"모델 설정 파일을 찾을 수 없습니다: {path}")
        try:
            with path.open("r", encoding="utf-8") as file:
                config = json.load(file)
        except (json.JSONDecodeError, OSError) as error:
            raise ValueError(f"모델 설정 파일을 읽을 수 없습니다: {path}") from error
        if not isinstance(config, dict):
            raise ValueError("모델 설정의 최상위 값은 객체여야 합니다.")
        return config

    @property
    def version(self) -> str:
        return self._version

    @property
    def embedding_dimension(self) -> int:
        return self._embedding_dimension

    @property
    def image_size(self) -> tuple[int, int]:
        return self._image_height, self._image_width

    @property
    def operating_point(self) -> str:
        return self._operating_point

    @property
    def similarity_threshold(self) -> float:
        return self._similarity_threshold

    def _validate_model_contract(self) -> None:
        """배포된 모델이 코드가 기대하는 입력·출력 규격과 일치하는지 확인한다."""
        inputs = self._session.get_inputs()
        outputs = self._session.get_outputs()
        if len(inputs) != 1 or inputs[0].name != "image":
            raise ValueError("ONNX 모델은 'image' 입력 하나를 제공해야 합니다.")
        if len(outputs) != 1 or outputs[0].name != "embedding":
            raise ValueError("ONNX 모델은 'embedding' 출력 하나를 제공해야 합니다.")
        if inputs[0].type != "tensor(float)" or outputs[0].type != "tensor(float)":
            raise ValueError("ONNX 모델 입출력은 float32 tensor여야 합니다.")
        if list(inputs[0].shape[1:]) != [
            1,
            self._image_height,
            self._image_width,
        ]:
            raise ValueError(f"ONNX 입력 shape이 올바르지 않습니다: {inputs[0].shape}")
        if list(outputs[0].shape[1:]) != [self._embedding_dimension]:
            raise ValueError(f"ONNX 출력 shape이 올바르지 않습니다: {outputs[0].shape}")

    def infer(self, image: Sequence[Sequence[float]]) -> list[float]:
        """정규화된 흑백 패턴 이미지 하나에서 L2 정규화 임베딩을 추출한다."""
        try:
            image_array = np.asarray(image, dtype=np.float32)
        except (TypeError, ValueError) as error:
            raise ValueError("모델 입력 이미지는 숫자로 구성되어야 합니다.") from error
        expected_shape = (self._image_height, self._image_width)
        if image_array.shape != expected_shape:
            raise ValueError(
                f"모델 입력 이미지는 {self._image_height}x{self._image_width}여야 합니다; "
                f"입력 shape: {image_array.shape}"
            )
        if not np.isfinite(image_array).all():
            raise ValueError("모델 입력 이미지는 유한한 값만 포함해야 합니다.")
        if np.any(image_array < 0.0) or np.any(image_array > 1.0):
            raise ValueError("모델 입력 이미지 값은 0.0~1.0 범위여야 합니다.")

        batch = image_array[np.newaxis, np.newaxis, :, :]
        embedding_batch = np.asarray(
            self._session.run(["embedding"], {"image": batch})[0],
            dtype=np.float32,
        )
        expected_output_shape = (1, self._embedding_dimension)
        if embedding_batch.shape != expected_output_shape:
            raise ValueError(
                f"ONNX 출력 shape이 올바르지 않습니다: {embedding_batch.shape}"
            )
        if not np.isfinite(embedding_batch).all():
            raise ValueError("ONNX 모델 출력은 유한한 값만 포함해야 합니다.")

        # 코사인 유사도 비교가 임베딩 크기의 영향을 받지 않도록 단위 벡터로 맞춘다.
        embedding = embedding_batch[0]
        norm = float(np.linalg.norm(embedding))
        if not math.isfinite(norm) or norm <= 1e-12:
            raise ValueError("ONNX 모델이 유효하지 않은 zero-norm 임베딩을 생성했습니다.")
        return (embedding / norm).astype(np.float32, copy=False).tolist()
