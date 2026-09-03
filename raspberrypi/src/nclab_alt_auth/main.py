"""Raspberry Pi 관리자·인증 애플리케이션의 실행 진입점."""

from .cli import main as run_cli


def main() -> int:
    """관리자용 명령행 인터페이스를 실행하고 종료 코드를 반환한다."""
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
