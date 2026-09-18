"""A progress line for runs that take a while.

The scraper is deliberately slow -- serial requests with a delay -- so a long
run needs to say what it is doing. Attached to a terminal this redraws one line
in place; piped to a file it prints a plain update every so often instead, so
logs stay readable.
"""

from __future__ import annotations

import logging
import shutil
import sys
import time

BAR_WIDTH = 22
QUIET_INTERVAL = 15.0  # seconds between updates when not on a terminal


def human_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


class Progress:
    """One live line on stderr. Safe to use when nothing is watching."""

    active: "Progress | None" = None

    def __init__(self, stream=None, enabled: bool = True):
        self.stream = stream or sys.stderr
        self.tty = enabled and hasattr(self.stream, "isatty") and self.stream.isatty()
        self.enabled = enabled
        self.started = time.monotonic()
        self.label = ""
        self.done = 0
        self.total: int | None = None
        self.note = ""
        self.live = False
        self._drawn = False
        self._last_print = 0.0

    # -- lifecycle -------------------------------------------------------

    def start(self, label: str, total: int | None = None) -> None:
        self.live = True
        self.label = label
        self.total = total
        self.done = 0
        self.note = ""
        self._last_print = 0.0
        self.render(force=True)

    def update(self, done: int | None = None, total: int | None = None, note: str | None = None) -> None:
        if done is not None:
            self.done = done
        if total is not None:
            self.total = total
        if note is not None:
            self.note = note
        self.render()

    def finish(self, message: str = "") -> None:
        self.live = False
        self.clear()
        if message and self.enabled:
            print(message, file=self.stream, flush=True)

    # -- drawing ---------------------------------------------------------

    def clear(self) -> None:
        if self._drawn and self.tty:
            self.stream.write("\r\033[2K")
            self.stream.flush()
        self._drawn = False

    def render(self, force: bool = False) -> None:
        if not self.enabled or not self.live:
            return
        now = time.monotonic()
        if not self.tty:
            # Without a terminal, say something occasionally rather than never.
            if not force and now - self._last_print < QUIET_INTERVAL:
                return
            self._last_print = now
            print("  " + self.text(), file=self.stream, flush=True)
            return
        self.stream.write("\r\033[2K  " + self.text(shutil.get_terminal_size((100, 20)).columns - 4))
        self.stream.flush()
        self._drawn = True

    def text(self, width: int | None = None) -> str:
        elapsed = time.monotonic() - self.started
        parts = [self.label] if self.label else []

        if self.total:
            filled = int(BAR_WIDTH * min(self.done, self.total) / self.total)
            parts.append("█" * filled + "░" * (BAR_WIDTH - filled))
            parts.append(f"{self.done}/{self.total}")
        elif self.done:
            parts.append(str(self.done))

        if self.note:
            parts.append(self.note)
        parts.append(human_time(elapsed))

        # Only guess at a finish once there is enough of a rate to guess from;
        # an estimate off the first couple of items swings wildly.
        if self.total and self.done >= 5 and self.done < self.total:
            remaining = elapsed / self.done * (self.total - self.done)
            parts.append(f"~{human_time(remaining)} left")

        line = "  ".join(parts)
        if width and len(line) > width > 3:
            line = line[: width - 1] + "…"
        return line


class ProgressHandler(logging.StreamHandler):
    """Log handler that gets out of the progress line's way."""

    def emit(self, record):
        bar = Progress.active
        # Only a terminal has a line to wipe and restore; when output is piped
        # the bar prints on its own schedule and must not echo after every log.
        redraw = bar is not None and bar.tty and bar.live
        if redraw:
            bar.clear()
        super().emit(record)
        if redraw:
            bar.render(force=True)
