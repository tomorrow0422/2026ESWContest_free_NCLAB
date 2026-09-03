"""Curses screen adapter for the terminal-independent TUI workflows."""

from __future__ import annotations

from types import ModuleType
from typing import Callable, TypeVar

from .tui import INPUT_POLL_MILLISECONDS, KeyEvent, Screen


class CursesScreen:
    """Translate a curses window into the testable :class:`Screen` API."""

    def __init__(self, window: object, curses_module: ModuleType) -> None:
        self._window = window
        self._curses = curses_module
        self._window.keypad(True)
        self._window.timeout(INPUT_POLL_MILLISECONDS)
        try:
            self._curses.curs_set(0)
        except self._curses.error:
            pass

    def get_size(self) -> tuple[int, int]:
        return self._window.getmaxyx()

    def clear(self) -> None:
        self._window.erase()

    def draw_text(self, row: int, column: int, text: str, *, selected: bool = False) -> None:
        rows, columns = self.get_size()
        if row < 0 or row >= rows or column < 0 or column >= columns:
            return
        attribute = self._curses.A_REVERSE if selected else self._curses.A_NORMAL
        try:
            self._window.addnstr(row, column, text, max(columns - column - 1, 0), attribute)
        except self._curses.error:
            return

    def refresh(self) -> None:
        self._window.refresh()

    def read_key(self) -> KeyEvent:
        key = self._window.getch()
        if key == self._curses.KEY_UP:
            return KeyEvent.UP
        if key == self._curses.KEY_DOWN:
            return KeyEvent.DOWN
        if key in {10, 13, getattr(self._curses, "KEY_ENTER", -1)}:
            return KeyEvent.ENTER
        if key == 27:
            return KeyEvent.BACK
        if key == self._curses.KEY_RESIZE:
            return KeyEvent.RESIZE
        return KeyEvent.IGNORE

    def read_text(self, max_length: int) -> str | None:
        try:
            self._window.timeout(-1)
            self._curses.noecho()
            self._curses.curs_set(1)
            start_row, start_column = self._window.getyx()
            _rows, columns = self.get_size()
            display_width = min(max_length, max(columns - start_column - 1, 0))
            characters: list[str] = []
            while True:
                key = self._window.get_wch()
                if key == "\x1b":
                    return None
                if key in {"\n", "\r"} or key == getattr(self._curses, "KEY_ENTER", -1):
                    return "".join(characters)
                if key in {"\b", "\x7f"} or key == getattr(
                    self._curses, "KEY_BACKSPACE", -1
                ):
                    if characters:
                        characters.pop()
                elif isinstance(key, str) and key.isprintable() and len(characters) < max_length:
                    characters.append(key)
                else:
                    continue
                self._window.move(start_row, start_column)
                self._window.clrtoeol()
                if display_width:
                    self._window.addnstr(
                        start_row,
                        start_column,
                        "".join(characters)[-display_width:],
                        display_width,
                        self._curses.A_NORMAL,
                    )
                self._window.refresh()
        except (UnicodeError, self._curses.error):
            return ""
        finally:
            self._curses.noecho()
            self._window.timeout(INPUT_POLL_MILLISECONDS)
            try:
                self._curses.curs_set(0)
            except self._curses.error:
                pass


Result = TypeVar("Result")


def run_curses(
    application: Callable[[Screen], Result],
    *,
    curses_module: ModuleType | None = None,
) -> Result:
    """Run an application through ``curses.wrapper`` for terminal recovery."""
    if curses_module is None:
        try:
            import curses as curses_module
        except ImportError as error:
            raise RuntimeError("이 환경에서는 curses TUI를 사용할 수 없습니다.") from error

    def wrapped(window: object) -> Result:
        return application(CursesScreen(window, curses_module))

    return curses_module.wrapper(wrapped)
