"""Raspberry Pi 관리자 애플리케이션의 명령행 인자와 종료 정책을 정의한다."""

from __future__ import annotations

import argparse
from pathlib import Path

from .application import SerialRuntime, build_service, run_tui
from .storage.errors import StorageError


def create_parser() -> argparse.ArgumentParser:
    """프로그램 실행에 필요한 명령행 인자를 정의한다."""
    parser = argparse.ArgumentParser(description="Raspberry Pi 압력 패턴 인증 관리자 앱")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="인증 데이터 저장 경로",
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("shell", help="관리자 메뉴 시작 (기본값)")
    return parser


def main() -> int:
    """모델과 저장소를 초기화한 뒤 관리자 메뉴를 실행한다."""
    args = create_parser().parse_args()
    try:
        service = build_service(args.data_dir)
    except (FileNotFoundError, ValueError) as error:
        # 공개 저장소에는 모델 가중치가 없으므로 시연 Pi에 모델이 없으면 명확하게 종료한다.
        print(f"모델 초기화 오류: {error}")
        return 4
    except StorageError as error:
        print(f"저장소 오류: {error}")
        return 3

    connection: SerialRuntime | None = None
    try:
        connection = SerialRuntime(service)
        return run_tui(service, connection)
    except StorageError as error:
        print(f"저장소 오류: {error}")
        return 3
    finally:
        if connection is not None:
            connection.close()
