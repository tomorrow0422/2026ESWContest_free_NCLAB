"""시연 Raspberry Pi에 별도로 설치된 AI 모델 경로를 관리한다."""

from __future__ import annotations

import os
from pathlib import Path


DEFAULT_MODEL_DIR = Path("/opt/nclab-alt-auth/model")
MODEL_FILENAME = "signature_embedding_v1.onnx"
CONFIG_FILENAME = "model_config.json"


def model_paths() -> tuple[Path, Path]:
    """환경변수 또는 기본 설치 위치에서 모델과 설정 파일 경로를 반환한다.

    학습된 모델 가중치는 공개 저장소에 포함하지 않는다. 대회 시연용 Raspberry Pi에는
    모델 파일과 설정 파일을 별도로 설치하고, 필요하면 환경변수로 위치를 지정한다.
    """
    model_path = Path(
        os.environ.get("NCLAB_MODEL_PATH", DEFAULT_MODEL_DIR / MODEL_FILENAME)
    ).expanduser()
    config_path = Path(
        os.environ.get("NCLAB_MODEL_CONFIG_PATH", DEFAULT_MODEL_DIR / CONFIG_FILENAME)
    ).expanduser()

    missing = [str(path) for path in (model_path, config_path) if not path.is_file()]
    if missing:
        joined = ", ".join(missing)
        raise FileNotFoundError(
            "AI 모델 파일을 찾을 수 없습니다. 시연 Raspberry Pi에 모델을 설치하거나 "
            "NCLAB_MODEL_PATH와 NCLAB_MODEL_CONFIG_PATH를 설정하세요: "
            f"{joined}"
        )

    return model_path, config_path
