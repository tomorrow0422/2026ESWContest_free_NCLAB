"""압력 패턴 인증 시스템의 애플리케이션 구성과 시리얼 실행 상태를 관리한다."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from .authentication import PatternAuthService
from .enrollment_data import User
from .inference import OnnxEmbeddingModel
from .model_assets import model_paths
from .serial.protocol import ArduinoProtocolClient
from .serial.session import ArduinoPatternSession
from .serial.transport import ArduinoSerialTransport
from .storage.calibration import CalibrationRepository
from .storage.enrollment import EnrollmentRepository
from .tui import (
    NotificationQueue,
    Screen,
    SerialSessionWorker,
    TuiApplication,
    UserListEntry,
    run_curses,
)


DEFAULT_SERIAL_TIMEOUT = 10.0


def discover_serial_ports() -> tuple[tuple[str, str], ...]:
    """현재 Raspberry Pi에서 감지되는 시리얼 장치와 설명을 반환한다."""
    try:
        from serial.tools import list_ports  # type: ignore[import-not-found]
    except ImportError as error:
        raise RuntimeError("pyserial 설치를 확인하세요: python -m pip install -e .") from error

    ports: dict[str, str] = {}
    for port in list_ports.comports():
        device = str(getattr(port, "device", "")).strip()
        if not device:
            continue
        description = str(getattr(port, "description", "") or "").strip()
        ports[device] = description

    def natural_key(item: tuple[str, str]) -> tuple[tuple[int, object], ...]:
        """ttyACM2가 ttyACM10보다 먼저 표시되도록 포트 이름을 자연 정렬한다."""
        return tuple(
            (0, int(part)) if part.isdecimal() else (1, part.casefold())
            for part in re.split(r"(\d+)", item[0])
        )

    return tuple(sorted(ports.items(), key=natural_key))


def build_service(data_dir: Path) -> PatternAuthService:
    """AI 모델, 사용자 저장소와 캘리브레이션 저장소를 묶어 인증 서비스를 생성한다."""
    # 모델 가중치는 공개 저장소에 포함하지 않고 시연 Raspberry Pi에 별도로 설치한다.
    model_path, config_path = model_paths()
    model = OnnxEmbeddingModel(
        model_path,
        config_path,
        operating_point="balanced",
    )
    return PatternAuthService(
        EnrollmentRepository(data_dir, embedding_dimension=model.embedding_dimension),
        model,
        CalibrationRepository(data_dir),
    )


def list_user_entries(service: PatternAuthService) -> tuple[UserListEntry, ...]:
    """관리자 화면에 표시할 사용자 상태 목록을 생성한다."""
    enrollments = service.enrollments.all()
    entries = []
    for record in enrollments:
        statuses = []
        if record.model_version != service.model.version:
            statuses.append(
                f"재등록 필요: 모델 {record.model_version} -> {service.model.version}"
            )
        failure_count, lock_seconds = service.authentication_failure_status(
            record.user_id
        )
        if lock_seconds > 0:
            statuses.append(f"인증 잠금: {lock_seconds}초 남음")
        elif failure_count > 0:
            statuses.append(f"인증 실패: {failure_count}/{service.max_attempts}")
        entries.append(
            UserListEntry(
                record.user_id,
                record.name,
                " / ".join(statuses) if statuses else "정상",
            )
        )
    return tuple(entries)


def list_user_lines(service: PatternAuthService) -> tuple[str, ...]:
    """텍스트 기반 호출부에서 사용할 사용자 목록 문자열을 반환한다."""
    lines = []
    for entry in list_user_entries(service):
        suffix = "" if entry.status == "정상" else f" [{entry.status}]"
        lines.append(f"{entry.user_id}: {entry.name}{suffix}")
    return tuple(lines)


def make_serial_session(
    device: ArduinoProtocolClient,
    service: PatternAuthService,
) -> ArduinoPatternSession:
    """Arduino 통신 이벤트를 등록·인증·캘리브레이션 서비스와 연결한다."""
    session = ArduinoPatternSession(
        device,
        service.is_enrolled,
        service.authenticate,
        lambda request, first, second: service.register(
            User(request.user_id, request.name), first, second
        ),
        service.calibrate,
        service.get_calibration,
    )
    session.set_authentication_failure_status_provider(
        service.authentication_failure_status
    )
    session.enable_connection_synchronization()
    session.enable_heartbeat()
    return session


class SerialRuntime:
    """관리자 앱에서 사용하는 시리얼 연결, 세션과 백그라운드 작업을 관리한다."""

    def __init__(self, service: PatternAuthService) -> None:
        self.service = service
        self.notifications = NotificationQueue()
        self._port: str | None = None
        self._baudrate: int | None = None
        self._transport: ArduinoSerialTransport | None = None
        self._session: ArduinoPatternSession | None = None
        self._worker: SerialSessionWorker | None = None

    @property
    def session(self) -> ArduinoPatternSession | None:
        return self._session

    def available_ports(self) -> tuple[tuple[str, str], ...]:
        """현재 선택 가능한 시리얼 포트 목록을 반환한다."""
        return discover_serial_ports()

    def connect(self, port: str, baudrate: int) -> None:
        """기존 연결을 종료하고 선택한 포트와 보드레이트로 Arduino에 연결한다."""
        normalized_port = port.strip()
        if not normalized_port:
            raise ValueError("시리얼 포트를 입력하세요.")
        if not isinstance(baudrate, int) or isinstance(baudrate, bool) or baudrate <= 0:
            raise ValueError("보드레이트는 양의 정수여야 합니다.")

        self.close()
        self.notifications.drain()
        transport = ArduinoSerialTransport(
            normalized_port,
            baudrate,
            DEFAULT_SERIAL_TIMEOUT,
        )
        try:
            session = make_serial_session(
                ArduinoProtocolClient(transport, self.notifications.publish),
                self.service,
            )
            worker = SerialSessionWorker(session)
            worker.start()
        except Exception:
            transport.close()
            raise

        self._port = normalized_port
        self._baudrate = baudrate
        self._transport = transport
        self._session = session
        self._worker = worker

    def status(self) -> str:
        """관리자 화면에 표시할 현재 Pi·Arduino 연결 상태를 반환한다."""
        failure = self._disconnect_failed_worker()
        if failure is not None:
            self.notifications.publish(failure)
        if self._session is None:
            return "시리얼 미연결"
        return (
            f"시리얼 {self._port} @ {self._baudrate} / "
            f"Pi {self._session.status()} / "
            f"Arduino {self._session.arduino_status()}"
        )

    def drain_notifications(self) -> tuple[str, ...]:
        """아직 표시하지 않은 통신 알림을 반환한다."""
        messages = list(self.notifications.drain())
        failure = self._disconnect_failed_worker()
        if failure is not None:
            messages.append(failure)
        return tuple(messages)

    def _disconnect_failed_worker(self) -> str | None:
        failure = self._worker.failure_message() if self._worker is not None else None
        if failure is not None:
            self.close()
        return failure

    def close(self) -> None:
        """현재 시리얼 작업을 종료하고 연결 자원을 안전하게 해제한다."""
        worker, transport = self._worker, self._transport
        self._worker = None
        self._session = None
        self._transport = None
        self._port = None
        self._baudrate = None
        if worker is not None:
            worker.stop_and_join()
        if transport is not None:
            transport.close()


def run_tui(
    service: PatternAuthService,
    connection: SerialRuntime,
    *,
    runner: Callable[[Callable[[Screen], int]], int] = run_curses,
) -> int:
    """Raspberry Pi 관리자용 메뉴 인터페이스를 실행한다."""
    return runner(
        lambda screen: TuiApplication(
            screen,
            connection=connection,
            get_calibration=service.get_calibration,
            list_users=lambda: list_user_entries(service),
            update_user=service.update_user,
            delete_user=service.delete_user,
        ).run()
    )
