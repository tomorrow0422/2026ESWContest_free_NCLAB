"""Testable terminal menu primitives with a lazily loaded curses adapter."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from enum import Enum, auto
from queue import Empty, Queue
from threading import Event, Thread
from typing import Callable, Protocol, Sequence

from .identity import validate_user_id
from .preprocessing import ADC_MAX
from .serial.protocol import PressureMonitorSample


MIN_TERMINAL_ROWS = 9
MIN_TERMINAL_COLUMNS = 32
INPUT_POLL_MILLISECONDS = 100
MIN_MONITOR_ROWS = 16
MIN_MONITOR_COLUMNS = 48
PRESSURE_MONITOR_WINDOW_SAMPLES = 1000
PRESSURE_MONITOR_STOP_WAIT_POLLS = 20
MENU_REFRESH = "__refresh__"
ARDUINO_DEFAULT_BAUDRATE = 115200
COMMON_BAUDRATES = (9600, 19200, 38400, 57600, 115200, 230400)
# Keep this synchronized with Arduino config.h::MAX_PRESSURE_ADC.
MAX_PRESSURE_ADC = 15564


class KeyEvent(Enum):
    """Normalized keys understood by menu logic."""

    UP = auto()
    DOWN = auto()
    ENTER = auto()
    BACK = auto()
    RESIZE = auto()
    IGNORE = auto()


class Screen(Protocol):
    """Rendering boundary used by the menu without depending on curses."""

    def get_size(self) -> tuple[int, int]: ...

    def clear(self) -> None: ...

    def draw_text(self, row: int, column: int, text: str, *, selected: bool = False) -> None: ...

    def refresh(self) -> None: ...

    def read_key(self) -> KeyEvent: ...

    def read_text(self, max_length: int) -> str | None: ...


class DeviceSession(Protocol):
    """Device operations required by the administrator menu."""

    def status(self) -> str: ...

    def arduino_status(self) -> str: ...

    def has_active_operation(self) -> bool: ...

    def begin_registration(self, user_id: str, name: str) -> None: ...

    def begin_calibration(self) -> None: ...

    def cancel_active_operation(self) -> bool: ...

    def begin_pressure_monitor(self) -> None: ...

    def stop_pressure_monitor(self) -> bool: ...

    def drain_pressure_monitor_samples(self) -> tuple[PressureMonitorSample, ...]: ...


class DeviceConnection(Protocol):
    """Runtime-owned serial connection used by the menu."""

    @property
    def session(self) -> DeviceSession | None: ...

    def available_ports(self) -> tuple[tuple[str, str], ...]: ...

    def connect(self, port: str, baudrate: int) -> None: ...

    def status(self) -> str: ...

    def drain_notifications(self) -> tuple[str, ...]: ...


class BackgroundSession(DeviceSession, Protocol):
    """Serial session operations needed by the background worker."""

    @property
    def failure(self) -> Exception | None: ...

    def serve_forever(self) -> int: ...

    def stop(self) -> None: ...


class NotificationQueue:
    """Thread-safe boundary between serial callbacks and the curses renderer."""

    def __init__(self) -> None:
        self._messages: Queue[str] = Queue()

    def publish(self, message: str) -> None:
        self._messages.put(message)

    def drain(self) -> tuple[str, ...]:
        messages: list[str] = []
        while True:
            try:
                messages.append(self._messages.get_nowait())
            except Empty:
                return tuple(messages)


class SerialSessionWorker:
    """Own a serial session thread and report failures without printing."""

    def __init__(self, session: BackgroundSession) -> None:
        self.session = session
        self.finished = Event()
        self._stopping = Event()
        self._result: int | None = None
        self._error: Exception | None = None
        self._thread = Thread(
            target=self._serve,
            name="arduino-pattern-session",
            daemon=True,
        )

    def _serve(self) -> None:
        try:
            self._result = self.session.serve_forever()
        except Exception as error:
            self._error = error
        finally:
            self.finished.set()

    def start(self) -> None:
        self._thread.start()

    def failure_message(self) -> str | None:
        failure = self.session.failure
        if failure is not None:
            return f"시리얼 서비스를 종료합니다: {failure}"
        if self._error is not None:
            return f"시리얼 worker가 예기치 않은 오류로 종료됐습니다: {self._error}"
        if self.finished.is_set() and not self._stopping.is_set():
            exit_code = self._result if self._result is not None else "알 수 없음"
            return f"시리얼 worker가 예기치 않게 종료됐습니다 (종료 코드 {exit_code})."
        return None

    def stop_and_join(self, timeout: float = 1.5) -> bool:
        self._stopping.set()
        self.session.stop()
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()


@dataclass(frozen=True)
class MenuItem:
    """One selectable menu value and its displayed label."""

    value: str
    label: str


@dataclass
class Menu:
    """Pure menu selection state."""

    title: str
    items: tuple[MenuItem, ...]
    selected_index: int = 0

    def __post_init__(self) -> None:
        if not self.items:
            raise ValueError("메뉴에는 하나 이상의 항목이 필요합니다.")
        if not 0 <= self.selected_index < len(self.items):
            raise ValueError("선택된 메뉴 위치가 항목 범위를 벗어났습니다.")

    @property
    def selected_item(self) -> MenuItem:
        return self.items[self.selected_index]

    def move_up(self) -> None:
        self.selected_index = (self.selected_index - 1) % len(self.items)

    def move_down(self) -> None:
        self.selected_index = (self.selected_index + 1) % len(self.items)


def render_menu(
    screen: Screen,
    menu: Menu,
    *,
    status: str = "",
    notification: str = "",
) -> None:
    """Render one complete frame, including compact small-terminal feedback."""
    rows, columns = screen.get_size()
    screen.clear()
    if rows < MIN_TERMINAL_ROWS or columns < MIN_TERMINAL_COLUMNS:
        screen.draw_text(0, 0, "터미널 크기가 너무 작습니다.")
        screen.draw_text(
            1,
            0,
            f"최소 {MIN_TERMINAL_COLUMNS}x{MIN_TERMINAL_ROWS}, 현재 {columns}x{rows}",
        )
        screen.refresh()
        return

    screen.draw_text(0, 0, menu.title)
    for index, item in enumerate(menu.items):
        screen.draw_text(
            index + 2,
            2,
            item.label,
            selected=index == menu.selected_index,
        )
    footer_row = rows - 2
    if status:
        screen.draw_text(footer_row, 0, f"상태: {status}")
    if notification:
        screen.draw_text(footer_row + 1, 0, notification)
    screen.refresh()


def choose_menu_item(
    screen: Screen,
    menu: Menu,
    *,
    status: str = "",
    notification: str = "",
) -> str | None:
    """Run a menu until Enter selects a value or ESC requests back/cancel."""
    while True:
        render_menu(screen, menu, status=status, notification=notification)
        event = screen.read_key()
        if event is KeyEvent.UP:
            menu.move_up()
        elif event is KeyEvent.DOWN:
            menu.move_down()
        elif event is KeyEvent.ENTER:
            return menu.selected_item.value
        elif event is KeyEvent.BACK:
            return None
        elif event in {KeyEvent.RESIZE, KeyEvent.IGNORE}:
            continue


def prompt_text(screen: Screen, title: str, prompt: str, *, max_length: int) -> str | None:
    """Read trimmed text, treating ESC as cancellation."""
    rows, _ = screen.get_size()
    screen.clear()
    screen.draw_text(0, 0, title)
    screen.draw_text(2, 0, prompt)
    screen.draw_text(max(rows - 1, 0), 0, "ESC: 취소")
    # Draw the prompt last so curses leaves the cursor on the actual input row.
    screen.draw_text(3, 0, "> ")
    screen.refresh()
    value = screen.read_text(max_length)
    return None if value is None else value.strip()


@dataclass(frozen=True)
class RegistrationDetails:
    user_id: str
    name: str


@dataclass(frozen=True)
class UserListEntry:
    """Structured user data rendered by the administrator list."""

    user_id: str
    name: str
    status: str = "정상"


def format_user_table(entries: Sequence[UserListEntry]) -> tuple[str, ...]:
    """Render aligned user rows without coupling persistence to curses."""
    headers = ("ID", "이름", "상태")
    rows = [(entry.user_id, entry.name, entry.status) for entry in entries]
    widths = tuple(
        max(len(header), *(len(row[index]) for row in rows))
        for index, header in enumerate(headers)
    )

    def render(row: tuple[str, str, str]) -> str:
        return " | ".join(value.ljust(widths[index]) for index, value in enumerate(row))

    return (
        render(headers),
        "-+-".join("-" * width for width in widths),
        *(render(row) for row in rows),
    )


def choose_user(
    screen: Screen,
    title: str,
    entries: Sequence[UserListEntry],
) -> UserListEntry | None:
    """Select one registered user by ID."""
    if not entries:
        return None
    selected_id = choose_menu_item(
        screen,
        Menu(
            title,
            tuple(
                MenuItem(entry.user_id, f"{entry.user_id} | {entry.name}")
                for entry in entries
            ),
        ),
    )
    if selected_id is None:
        return None
    return next(entry for entry in entries if entry.user_id == selected_id)


def run_user_reenrollment_flow(
    screen: Screen,
    session: DeviceSession | None,
    get_calibration: Callable[[], object | None],
    entries: Sequence[UserListEntry],
) -> str:
    """Confirm and start registration again for an existing user."""
    if session is None:
        return "사용자 재등록 전에 시리얼 연결 설정을 완료하세요."
    if session.has_active_operation():
        return "다른 장치 작업이 진행 중입니다."
    if get_calibration() is None:
        return "먼저 센서 캘리브레이션을 완료하세요."
    if not entries:
        return "등록된 사용자가 없습니다."

    selected = choose_user(screen, "재등록할 사용자 선택", entries)
    if selected is None:
        return "사용자 재등록을 취소했습니다."
    confirmed = choose_menu_item(
        screen,
        Menu(
            "사용자 재등록 확인",
            (
                MenuItem("confirm", "기존 패턴을 교체하고 재등록"),
                MenuItem("cancel", "취소"),
            ),
        ),
        status=f"ID {selected.user_id} / 이름 {selected.name}",
    )
    if confirmed != "confirm":
        return "사용자 재등록을 취소했습니다."
    try:
        session.begin_registration(selected.user_id, selected.name)
    except (RuntimeError, ValueError) as error:
        return f"재등록 시작 실패: {error}"
    return f"{selected.user_id} 사용자의 재등록을 시작했습니다."


def run_user_edit_flow(
    screen: Screen,
    entries: Sequence[UserListEntry],
    update_user: Callable[[str, str, str], bool],
) -> str:
    """Edit a registered user's ID and name after explicit confirmation."""
    if not entries:
        return "등록된 사용자가 없습니다."
    selected = choose_user(screen, "정보를 수정할 사용자 선택", entries)
    if selected is None:
        return "사용자 정보 수정을 취소했습니다."

    new_user_id = prompt_text(
        screen,
        "사용자 정보 수정",
        f"새 사용자 ID (Enter: {selected.user_id})",
        max_length=10,
    )
    if new_user_id is None:
        return "사용자 정보 수정을 취소했습니다."
    new_user_id = new_user_id or selected.user_id
    try:
        validate_user_id(new_user_id)
    except ValueError as error:
        return f"사용자 ID 오류: {error}"

    new_name = prompt_text(
        screen,
        "사용자 정보 수정",
        f"새 이름 (Enter: {selected.name})",
        max_length=40,
    )
    if new_name is None:
        return "사용자 정보 수정을 취소했습니다."
    new_name = new_name or selected.name
    confirmed = choose_menu_item(
        screen,
        Menu(
            "사용자 정보 수정 확인",
            (
                MenuItem("confirm", "수정"),
                MenuItem("cancel", "취소"),
            ),
        ),
        status=f"{selected.user_id} -> {new_user_id} / {selected.name} -> {new_name}",
    )
    if confirmed != "confirm":
        return "사용자 정보 수정을 취소했습니다."
    try:
        changed = update_user(selected.user_id, new_user_id, new_name)
    except (RuntimeError, ValueError) as error:
        return f"사용자 정보 수정 실패: {error}"
    return (
        f"{new_user_id} 사용자 정보를 수정했습니다."
        if changed
        else "수정할 사용자를 찾을 수 없습니다."
    )


def run_user_delete_flow(
    screen: Screen,
    entries: Sequence[UserListEntry],
    delete_user: Callable[[str], bool],
) -> str:
    """Delete one selected user only after an explicit destructive confirmation."""
    if not entries:
        return "등록된 사용자가 없습니다."
    selected = choose_user(screen, "삭제할 사용자 선택", entries)
    if selected is None:
        return "사용자 삭제를 취소했습니다."

    confirmed = choose_menu_item(
        screen,
        Menu(
            "사용자 삭제 확인",
            (
                MenuItem("cancel", "취소"),
                MenuItem("confirm", "사용자와 등록 패턴 삭제"),
            ),
        ),
        status=f"ID {selected.user_id} / 이름 {selected.name}",
    )
    if confirmed != "confirm":
        return "사용자 삭제를 취소했습니다."
    try:
        deleted = delete_user(selected.user_id)
    except (RuntimeError, ValueError) as error:
        return f"사용자 삭제 실패: {error}"
    return (
        f"{selected.user_id} 사용자를 삭제했습니다."
        if deleted
        else "삭제할 사용자를 찾을 수 없습니다."
    )


def run_serial_settings_flow(screen: Screen, connection: DeviceConnection) -> str:
    """Collect ephemeral serial settings and connect the Arduino."""
    try:
        ports = connection.available_ports()
    except RuntimeError as error:
        return f"시리얼 포트 조회 실패: {error}"
    if not ports:
        return "연결된 시리얼 포트를 찾을 수 없습니다."

    selected_port = choose_menu_item(
        screen,
        Menu(
            "시리얼 포트 선택",
            tuple(
                MenuItem(
                    device,
                    f"{device} - {description}" if description else device,
                )
                for device, description in ports
            ),
        ),
        status="연결할 포트를 선택하세요.",
    )
    if selected_port is None:
        return "시리얼 연결 설정을 취소했습니다."

    baudrate_choice = choose_menu_item(
        screen,
        Menu(
            "보드레이트 선택",
            tuple(MenuItem(str(value), str(value)) for value in COMMON_BAUDRATES)
            + (MenuItem("custom", "직접 입력"),),
            selected_index=COMMON_BAUDRATES.index(ARDUINO_DEFAULT_BAUDRATE),
        ),
        status=f"Arduino 기본값: {ARDUINO_DEFAULT_BAUDRATE}",
    )
    if baudrate_choice is None:
        return "시리얼 연결 설정을 취소했습니다."
    if baudrate_choice == "custom":
        baudrate_text = prompt_text(
            screen,
            "시리얼 연결 설정",
            "보드레이트",
            max_length=10,
        )
        if baudrate_text is None:
            return "시리얼 연결 설정을 취소했습니다."
        if not baudrate_text.isascii() or not baudrate_text.isdecimal():
            return "보드레이트는 양의 정수여야 합니다."
        baudrate = int(baudrate_text)
        if baudrate <= 0:
            return "보드레이트는 양의 정수여야 합니다."
    else:
        baudrate = int(baudrate_choice)

    try:
        connection.connect(selected_port, baudrate)
    except (RuntimeError, ValueError) as error:
        return f"시리얼 연결 실패: {error}"
    message = f"시리얼 연결 완료: {selected_port} @ {baudrate}"
    if baudrate != ARDUINO_DEFAULT_BAUDRATE:
        message += f" (경고: Arduino 기본값은 {ARDUINO_DEFAULT_BAUDRATE}입니다.)"
    return message


def run_registration_flow(
    screen: Screen,
    session: DeviceSession | None,
    get_calibration: Callable[[], object | None],
    find_user: Callable[[str], UserListEntry | None] | None = None,
) -> str:
    """Collect, validate, confirm, and start one device registration."""
    if session is None:
        return "사용자 등록 전에 시리얼 연결 설정을 완료하세요."
    if session.has_active_operation():
        return "다른 장치 작업이 진행 중입니다."
    if get_calibration() is None:
        return "먼저 센서 캘리브레이션을 완료하세요."

    user_id = prompt_text(screen, "사용자 등록", "사용자 ID", max_length=10)
    if user_id is None:
        return "사용자 등록을 취소했습니다."
    try:
        validate_user_id(user_id)
    except ValueError as error:
        return f"사용자 ID 오류: {error}"

    existing = find_user(user_id) if find_user is not None else None
    if existing is not None:
        reenroll = choose_menu_item(
            screen,
            Menu(
                "기존 사용자 재등록 확인",
                (
                    MenuItem("cancel", "취소"),
                    MenuItem("confirm", "기존 패턴을 교체하고 재등록"),
                ),
            ),
            status=f"ID {existing.user_id} / 기존 이름 {existing.name}",
        )
        if reenroll != "confirm":
            return "기존 사용자 재등록을 취소했습니다."

    name_prompt = "이름" if existing is None else f"이름 (Enter: {existing.name})"
    name = prompt_text(screen, "사용자 등록", name_prompt, max_length=40)
    if name is None:
        return "사용자 등록을 취소했습니다."
    if not name and existing is not None:
        name = existing.name
    if not name:
        return "이름을 입력하세요."

    details = RegistrationDetails(user_id, name)
    confirmed = choose_menu_item(
        screen,
        Menu(
            "등록 정보 확인",
            (
                MenuItem("confirm", "재등록 시작" if existing else "등록 시작"),
                MenuItem("cancel", "취소"),
            ),
        ),
        status=f"ID {details.user_id} / 이름 {details.name}",
    )
    if confirmed != "confirm":
        return "사용자 등록을 취소했습니다."
    try:
        session.begin_registration(details.user_id, details.name)
    except ValueError as error:
        return f"등록 시작 실패: {error}"
    return f"{details.user_id} 사용자의 등록을 시작했습니다."


def show_lines(screen: Screen, title: str, lines: Sequence[str]) -> None:
    """Show read-only content until Enter or ESC returns to the menu."""
    while True:
        rows, _columns = screen.get_size()
        screen.clear()
        screen.draw_text(0, 0, title)
        for row, line in enumerate(lines[: max(rows - 3, 0)], start=2):
            screen.draw_text(row, 0, line)
        screen.draw_text(max(rows - 1, 0), 0, "Enter 또는 ESC: 돌아가기")
        screen.refresh()
        if screen.read_key() in {KeyEvent.ENTER, KeyEvent.BACK}:
            return


def _downsample_pressure_values(values: Sequence[int], width: int) -> tuple[int | None, ...]:
    """Fit ordered ADC values to graph columns while preserving short peaks."""
    if width <= 0:
        return ()
    if not values:
        return (None,) * width
    if len(values) <= width:
        return (None,) * (width - len(values)) + tuple(values)

    columns: list[int] = []
    for column in range(width):
        start = column * len(values) // width
        end = (column + 1) * len(values) // width
        columns.append(max(values[start:end]))
    return tuple(columns)


def _threshold_row(value: float, graph_height: int) -> int:
    normalized = min(max(value / ADC_MAX, 0.0), 1.0)
    return graph_height - 1 - round(normalized * (graph_height - 1))


def render_pressure_monitor(
    screen: Screen,
    values: Sequence[int],
    *,
    status: str,
    active_threshold: float | None,
    notification: str = "",
    stopping: bool = False,
) -> None:
    """Render current raw pressure and a rolling five-second terminal graph."""
    rows, columns = screen.get_size()
    screen.clear()
    if rows < MIN_MONITOR_ROWS or columns < MIN_MONITOR_COLUMNS:
        screen.draw_text(0, 0, "압력센서 화면을 표시하기에 터미널이 너무 작습니다.")
        screen.draw_text(
            1,
            0,
            f"최소 {MIN_MONITOR_COLUMNS}x{MIN_MONITOR_ROWS}, 현재 {columns}x{rows}",
        )
        screen.draw_text(max(rows - 1, 0), 0, "ESC 또는 Enter: 종료")
        screen.refresh()
        return

    current = values[-1] if values else None
    current_text = "수신 대기"
    maximum_state = "수신 대기"
    if current is not None:
        percentage = current / ADC_MAX * 100.0
        current_text = f"{current:5d} / {ADC_MAX} ({percentage:5.1f}%)"
        maximum_state = "도달" if current >= MAX_PRESSURE_ADC else "미도달"
    threshold_text = (
        f"> {active_threshold:.1f}"
        if active_threshold is not None and math.isfinite(active_threshold)
        else "미설정"
    )

    screen.draw_text(0, 0, "압력센서 실시간 확인")
    screen.draw_text(1, 0, "센서 점검 및 등록 전 패턴 연습 (값은 저장되지 않음)")
    screen.draw_text(2, 0, f"상태: {status}")
    screen.draw_text(3, 0, f"현재 ADC: {current_text}")
    screen.draw_text(
        4,
        0,
        f"활성 기준: {threshold_text} | 최대 기준: {MAX_PRESSURE_ADC} ({maximum_state})",
    )

    graph_start_row = 6
    graph_height = rows - 8
    graph_width = max(columns - 7, 1)
    graph_values = _downsample_pressure_values(values, graph_width)
    active_row = (
        _threshold_row(active_threshold, graph_height)
        if active_threshold is not None and math.isfinite(active_threshold)
        else None
    )
    maximum_row = _threshold_row(MAX_PRESSURE_ADC, graph_height)

    for graph_row in range(graph_height):
        if graph_row == 0:
            label = f"{ADC_MAX:5d}"
        elif graph_row == graph_height - 1:
            label = f"{0:5d}"
        else:
            label = "     "
        background = "=" if graph_row == maximum_row else "." if graph_row == active_row else " "
        cells = []
        row_floor = ADC_MAX * (graph_height - 1 - graph_row) / max(graph_height - 1, 1)
        for value in graph_values:
            cells.append("█" if value is not None and value >= row_floor else background)
        screen.draw_text(graph_start_row + graph_row, 0, f"{label}│{''.join(cells)}")

    if notification:
        screen.draw_text(rows - 2, 0, notification)
    footer = "진단 종료 확인 대기 중..." if stopping else "ESC 또는 Enter: 종료"
    screen.draw_text(rows - 1, 0, footer)
    screen.refresh()


def run_pressure_monitor_flow(
    screen: Screen,
    session: DeviceSession | None,
    get_calibration: Callable[[], object | None],
    *,
    get_status: Callable[[], str],
    poll_notification: Callable[[], str],
) -> str:
    """Start, display, and safely stop one administrator pressure monitor."""
    if session is None:
        return "압력센서 확인 전에 시리얼 연결 설정을 완료하세요."
    if session.has_active_operation():
        return "다른 장치 작업이 진행 중입니다."

    try:
        session.begin_pressure_monitor()
    except (RuntimeError, ValueError) as error:
        return f"압력센서 진단 시작 실패: {error}"

    record = get_calibration()
    raw_threshold = getattr(record, "active_threshold", None)
    active_threshold = (
        float(raw_threshold)
        if isinstance(raw_threshold, (int, float)) and not isinstance(raw_threshold, bool)
        else None
    )
    values: deque[int] = deque(maxlen=PRESSURE_MONITOR_WINDOW_SAMPLES)
    stopping = False
    stop_wait_polls = 0

    while True:
        notification = poll_notification()
        values.extend(sample.adc_value for sample in session.drain_pressure_monitor_samples())
        render_pressure_monitor(
            screen,
            tuple(values),
            status=get_status(),
            active_threshold=active_threshold,
            notification=notification,
            stopping=stopping,
        )

        if not session.has_active_operation():
            return (
                "압력센서 진단을 종료했습니다."
                if stopping
                else "압력센서 진단이 장치에서 종료됐습니다."
            )

        event = screen.read_key()
        if not stopping and event in {KeyEvent.ENTER, KeyEvent.BACK}:
            try:
                stopping = session.stop_pressure_monitor()
            except (RuntimeError, ValueError) as error:
                return f"압력센서 진단 종료 실패: {error}"
            if not stopping:
                return "압력센서 진단이 이미 종료됐습니다."
        elif stopping:
            stop_wait_polls += 1
            if stop_wait_polls >= PRESSURE_MONITOR_STOP_WAIT_POLLS:
                try:
                    session.cancel_active_operation()
                except (RuntimeError, ValueError) as error:
                    return f"압력센서 진단 종료 확인 실패: {error}"
                return "진단 종료 확인 시간이 초과되어 로컬 상태를 정리했습니다."


class TuiApplication:
    """Administrator-oriented menu workflow independent from curses details."""

    def __init__(
        self,
        screen: Screen,
        *,
        connection: DeviceConnection,
        get_calibration: Callable[[], object | None],
        list_users: Callable[[], Sequence[str | UserListEntry]],
        update_user: Callable[[str, str, str], bool] | None = None,
        delete_user: Callable[[str], bool] | None = None,
    ) -> None:
        self.screen = screen
        self.connection = connection
        self.get_calibration = get_calibration
        self.list_users = list_users
        self.update_user = update_user
        self.delete_user = delete_user
        self.notification = ""

    def _poll_background(self) -> None:
        messages = self.connection.drain_notifications()
        if messages:
            self.notification = messages[-1]

    def _poll_notification(self) -> str:
        self._poll_background()
        return self.notification

    def _choose(self, menu: Menu, *, refresh_on_operation_change: bool = False) -> str | None:
        initial_session = self.connection.session
        initial_active = (
            initial_session is not None and initial_session.has_active_operation()
        )
        while True:
            self._poll_background()
            current_session = self.connection.session
            current_active = (
                current_session is not None and current_session.has_active_operation()
            )
            if refresh_on_operation_change and current_active != initial_active:
                return MENU_REFRESH
            render_menu(
                self.screen,
                menu,
                status=self._status(),
                notification=self.notification,
            )
            event = self.screen.read_key()
            if event is KeyEvent.UP:
                menu.move_up()
            elif event is KeyEvent.DOWN:
                menu.move_down()
            elif event is KeyEvent.ENTER:
                return menu.selected_item.value
            elif event is KeyEvent.BACK:
                return None

    def _status(self) -> str:
        return self.connection.status()

    def _show_users(self) -> None:
        users = tuple(self.list_users())
        entries = tuple(user for user in users if isinstance(user, UserListEntry))
        if entries and len(entries) == len(users):
            lines: Sequence[str] = format_user_table(entries)
        else:
            lines = tuple(str(user) for user in users)
        show_lines(
            self.screen,
            "등록 사용자 목록",
            lines if lines else ("등록된 사용자가 없습니다.",),
        )

    def _user_entries(self) -> tuple[UserListEntry, ...]:
        return tuple(
            user for user in self.list_users() if isinstance(user, UserListEntry)
        )

    def _find_user(self, user_id: str) -> UserListEntry | None:
        return next(
            (user for user in self._user_entries() if user.user_id == user_id),
            None,
        )

    def _run_admin_menu(self) -> None:
        while True:
            session = self.connection.session
            active = session is not None and session.has_active_operation()
            if active:
                items = (
                    MenuItem("cancel-active", "진행 중 작업 취소"),
                    MenuItem("users", "등록 사용자 목록"),
                    MenuItem("back", "뒤로"),
                )
            else:
                base_items = [
                    MenuItem("serial-settings", "시리얼 연결 설정"),
                    MenuItem("register", "사용자 등록"),
                    MenuItem("users", "등록 사용자 목록"),
                ]
                if session is not None:
                    base_items.append(MenuItem("calibrate", "센서 캘리브레이션"))
                    base_items.append(MenuItem("pressure-monitor", "압력센서 실시간 확인"))
                    if self.update_user is not None:
                        base_items.append(MenuItem("reenroll-user", "사용자 재등록"))
                if self.update_user is not None:
                    base_items.append(MenuItem("edit-user", "사용자 정보 수정"))
                if self.delete_user is not None:
                    base_items.append(MenuItem("delete-user", "사용자 삭제"))
                base_items.append(MenuItem("back", "뒤로"))
                items = tuple(base_items)

            choice = self._choose(
                Menu("관리자 메뉴", items),
                refresh_on_operation_change=True,
            )
            if choice == MENU_REFRESH:
                continue
            self.notification = ""
            if choice in {None, "back"}:
                return
            if choice == "serial-settings":
                self.notification = run_serial_settings_flow(
                    self.screen,
                    self.connection,
                )
            elif choice == "register":
                self.notification = run_registration_flow(
                    self.screen,
                    session,
                    self.get_calibration,
                    self._find_user,
                )
            elif choice == "users":
                self._show_users()
            elif choice == "reenroll-user":
                self.notification = run_user_reenrollment_flow(
                    self.screen,
                    session,
                    self.get_calibration,
                    self._user_entries(),
                )
            elif choice == "edit-user" and self.update_user is not None:
                self.notification = run_user_edit_flow(
                    self.screen,
                    self._user_entries(),
                    self.update_user,
                )
            elif choice == "delete-user" and self.delete_user is not None:
                self.notification = run_user_delete_flow(
                    self.screen,
                    self._user_entries(),
                    self.delete_user,
                )
            elif choice == "calibrate" and session is not None:
                try:
                    session.begin_calibration()
                except ValueError as error:
                    self.notification = f"캘리브레이션 시작 실패: {error}"
                else:
                    self.notification = "센서 캘리브레이션을 시작했습니다."
            elif choice == "pressure-monitor":
                self.notification = run_pressure_monitor_flow(
                    self.screen,
                    session,
                    self.get_calibration,
                    get_status=self._status,
                    poll_notification=self._poll_notification,
                )
            elif choice == "cancel-active" and session is not None:
                self.notification = (
                    "진행 중인 작업을 취소했습니다."
                    if session.cancel_active_operation()
                    else "취소할 작업이 없습니다."
                )

    def run(self) -> int:
        """Run the main menu until the user chooses exit."""
        while True:
            choice = self._choose(
                Menu(
                    "메인 메뉴",
                    (
                        MenuItem("admin", "관리자 메뉴"),
                        MenuItem("status", "상태 확인"),
                        MenuItem("exit", "종료"),
                    ),
                )
            )
            self.notification = ""
            if choice in {None, "exit"}:
                return 0
            if choice == "admin":
                self._run_admin_menu()
            elif choice == "status":
                show_lines(self.screen, "상태 확인", (self._status(),))


from .tui_screen import CursesScreen, run_curses
