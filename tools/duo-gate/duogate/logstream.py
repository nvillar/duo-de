"""Live logcat capture with unique markers.

Each scripted action is bracketed by DuoGate markers written to the device log (adb shell log). Evidence for an action
is only taken from lines between its markers, so anything logged earlier (stale evidence) cannot satisfy a check, and a
marker that never shows up proves the capture is not live (INCOMPLETE). Only an allowlist of tags is kept, so the
evidence directory holds no unrelated, possibly personal, log content.
"""
from __future__ import annotations

import subprocess
import threading
import time

from .model import EvidenceError
from .parsers import LogLine, ProbeEvent, marker_id, parse_log_line, probe_event

KEEP_TAGS = {"DuoGate", "DuoProbe", "DuoDisplayAreaPolicy", "DuoDisplayMask", "POSTURE PROCESSOR"}
KEEP_LEVELS = {"AndroidRuntime": "EF", "DEBUG": "F", "libc": "F", "ActivityManager": "E", "InputDispatcher": "EW",
               "WindowManager": "E"}


def keep(line: LogLine) -> bool:
    if line.tag in KEEP_TAGS:
        return True
    levels = KEEP_LEVELS.get(line.tag)
    return levels is not None and line.level in levels


class LogBuffer:
    """Filtered log lines with sequence numbers; the offline (test) and live captures share this logic."""

    def __init__(self, path: str | None = None):
        self.lines: list[LogLine] = []
        self._cond = threading.Condition()
        self._file = open(path, "a", encoding="utf-8") if path else None

    @property
    def alive(self) -> bool:
        return True

    def feed(self, raw: str) -> None:
        ll = parse_log_line(raw)
        if ll is None or not keep(ll):
            return
        with self._cond:
            ll.seq = len(self.lines)
            self.lines.append(ll)
            if self._file:
                self._file.write(ll.raw + "\n")
                self._file.flush()
            self._cond.notify_all()

    def find_marker(self, mid: str) -> int | None:
        with self._cond:
            for ll in self.lines:
                if marker_id(ll) == mid:
                    return ll.seq
        return None

    def wait_marker(self, mid: str, timeout: float) -> int:
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                for ll in reversed(self.lines):
                    if marker_id(ll) == mid:
                        return ll.seq
                left = deadline - time.monotonic()
                if left <= 0 or not self.alive:
                    raise EvidenceError("log marker %s never arrived: logcat capture is not live" % mid)
                self._cond.wait(min(left, 0.5))

    def between(self, start: int, end: int | None = None) -> list[LogLine]:
        with self._cond:
            return [l for l in self.lines if l.seq > start and (end is None or l.seq < end)]

    def events(self, start: int, end: int | None = None) -> list[ProbeEvent]:
        return [e for e in (probe_event(l) for l in self.between(start, end)) if e is not None]

    def wait_for(self, start: int, predicate, timeout: float) -> bool:
        """Waits until predicate(events since start) is true; False on timeout (the caller decides INCOMPLETE)."""
        deadline = time.monotonic() + timeout
        while True:
            if predicate(self.events(start)):
                return True
            left = deadline - time.monotonic()
            if left <= 0 or not self.alive:
                return False
            with self._cond:
                self._cond.wait(min(left, 0.25))

    def latest(self, predicate, before: int | None = None) -> ProbeEvent | None:
        """The newest probe event satisfying predicate, at any point of the capture (before a sequence number)."""
        with self._cond:
            lines = list(self.lines)
        for l in reversed(lines):
            if before is not None and l.seq >= before:
                continue
            e = probe_event(l)
            if e is not None and predicate(e):
                return e
        return None

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None


class LogStream(LogBuffer):
    """adb logcat in a background process; lines are filtered and appended as they arrive."""

    def __init__(self, command: list[str], path: str):
        super().__init__(path)
        self._proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        for raw in iter(self._proc.stdout.readline, b""):
            self.feed(raw.decode("utf-8", "replace"))
        with self._cond:
            self._cond.notify_all()

    @property
    def alive(self) -> bool:
        return self._proc.poll() is None

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        super().close()
