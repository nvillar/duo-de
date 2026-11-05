"""Evidence capture (snapshots, screenshots) and the scripted device actions used by the gates."""
from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import time
from dataclasses import dataclass

from . import parsers
from .adb import Adb, AdbError
from .checks import DUO1, PROBE_PACKAGE, TEST_CLASSES, Geometry
from .logstream import LogBuffer, LogStream
from .model import EvidenceError, Rect
from .parsers import LogLine, ProbeEvent

KEY_HOME, KEY_BACK, KEY_POWER, KEY_ENTER, KEY_WAKEUP = 3, 4, 26, 66, 224

DUMPS = {
    "containers": "dumpsys activity containers",
    "activities": "dumpsys activity activities",
    "wdisplays": "dumpsys window displays",
    "windows": "dumpsys window windows",
    "input": "dumpsys input",
}

# Home buttons by label, for the uiautomator fallback (the probe's own targets event is preferred).
HOME_LABELS = {
    "Probe test": "probeTest",
    "Probe test 2 (own task)": "probeTest2",
    "Probe test (new instance)": "probeTestNew",
    "Probe locked (showWhenLocked)": "probeLocked",
    "Send launchDisplayId=0 (Quickstep bypass)": "toggle",
}
# Home target key -> (button label, activity class it starts)
HOME_BUTTONS = {
    "probeTest": ("Probe test", "ProbeTestActivity"),
    "probeTest2": ("Probe test 2 (own task)", "ProbeOtherTestActivity"),
    "probeTestNew": ("Probe test (new instance)", "ProbeTestActivity"),
    "probeLocked": ("Probe locked (showWhenLocked)", "ProbeLockedTestActivity"),
}


class Snapshot:
    """One set of dumps taken at the same moment (bounded by a marker), parsed lazily; parse errors raise
    EvidenceError so that a check reading them becomes INCOMPLETE."""

    def __init__(self, name: str, texts: dict, files: list[str] | None = None):
        self.name = name
        self.texts = texts
        self.files = files or []
        self._cache = {}

    def _get(self, kind, parser):
        if kind not in self._cache:
            text = self.texts.get(kind)
            if text is None:
                raise EvidenceError("%s not captured in snapshot %s" % (kind, self.name))
            self._cache[kind] = parser(text)
        return self._cache[kind]

    @property
    def containers(self):
        return self._get("containers", parsers.parse_containers)

    @property
    def activities(self):
        return self._get("activities", parsers.parse_activities)

    @property
    def wdisplays(self):
        return self._get("wdisplays", parsers.parse_window_displays)

    @property
    def windows(self):
        return self._get("windows", parsers.parse_window_windows)

    @property
    def input(self):
        return self._get("input", parsers.parse_input)

    @classmethod
    def load(cls, directory: str, name: str) -> "Snapshot":
        texts, files = {}, []
        for kind in DUMPS:
            p = os.path.join(directory, "%s-%s.txt" % (name, kind))
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    texts[kind] = f.read()
                files.append(p)
        return cls(name, texts, files)


@dataclass
class Step:
    """One bracketed action: its marker, the log window, and the snapshot taken after it settled."""
    marker: str
    start: int
    end: int
    events: list[ProbeEvent]
    lines: list[LogLine]
    snap: Snapshot | None
    settled: bool

    @property
    def evidence(self) -> list[str]:
        return ["marker " + self.marker] + (self.snap.files if self.snap else [])


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:80]


class Device:
    def __init__(self, adb: Adb, outdir: str, run_id: str, *, settle: float = 8.0, quiet: float = 0.8,
                 screens: str = "key", geo: Geometry = DUO1, live: bool = True):
        self.adb = adb
        self.outdir = outdir
        self.run_id = run_id
        self.settle = settle
        self.quiet = quiet
        self.screens = screens
        self.geo = geo
        self.remove_wait = 1.0
        self.snapdir = os.path.join(outdir, "snaps")
        os.makedirs(self.snapdir, exist_ok=True)
        self.log: LogBuffer = LogBuffer() if not live else self._start_log()
        self._snaps = 0
        self._marks = 0
        self._run_start: int | None = None      # seq of the first marker in the current capture
        self._earlier: list[LogLine] = []        # run lines of captures before a reboot

    # -- log ----------------------------------------------------------------------------------------------------------
    def _start_log(self) -> LogBuffer:
        return LogStream(self.adb.logcat_command(), os.path.join(self.outdir, "logcat.txt"))

    def restart_log(self) -> None:
        """After a reboot the logcat process is gone; markers keep their uniqueness across captures. Everything the
        new capture replays happened after the reboot, so it all belongs to the run."""
        self._earlier += self.run_lines()
        self.log.close()
        self.log = self._start_log()
        self._run_start = -1

    def run_lines(self) -> list[LogLine]:
        """Captured lines since the run started (replayed history before the first marker excluded)."""
        if self._run_start is None:
            return list(self._earlier)
        return self._earlier + self.log.between(self._run_start)

    def mark(self, step: str) -> tuple[str, int]:
        self._marks += 1
        mid = "%s.%d" % (self.run_id, self._marks)
        self.adb.shell("log -t DuoGate %s" % shlex.quote("mark id=%s step=%s" % (mid, _safe(step))))
        seq = self.log.wait_marker(mid, timeout=15)
        if self._run_start is None:
            self._run_start = seq - 1
        return mid, seq

    def end(self, mid: str) -> int:
        eid = mid + "e"
        self.adb.shell("log -t DuoGate %s" % shlex.quote("mark id=%s step=end" % eid))
        return self.log.wait_marker(eid, timeout=15)

    # -- evidence -----------------------------------------------------------------------------------------------------
    def _rel(self, path: str) -> str:
        return os.path.relpath(path, self.outdir).replace(os.sep, "/")

    def snap(self, name: str, screenshot: bool = False, kinds=tuple(DUMPS)) -> Snapshot:
        self._snaps += 1
        base = "%04d-%s" % (self._snaps, _safe(name.replace(self.run_id + ".", "m")))
        texts, files = {}, []
        for kind in kinds:
            text = self.adb.shell(DUMPS[kind], timeout=60)
            path = os.path.join(self.snapdir, "%s-%s.txt" % (base, kind))
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            texts[kind] = text
            files.append(self._rel(path))
        if screenshot or self.screens == "all":
            files.append(self.screenshot(base))
        return Snapshot(base, texts, files)

    def screenshot(self, name: str) -> str:
        if self.screens == "none":
            return "(screenshots disabled)"
        path = os.path.join(self.outdir, "screens", _safe(name) + ".png")
        self.adb.screenshot(path)
        return self._rel(path)

    def save_text(self, name: str, text: str) -> str:
        path = os.path.join(self.outdir, "extra", _safe(name) + ".txt")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return self._rel(path)

    def step(self, name: str, action, until=None, timeout: float | None = None, snapshot: bool = True,
             screenshot: bool = False, kinds=tuple(DUMPS)) -> Step:
        mid, start = self.mark(name)
        action()
        settled = True
        if until is not None:
            settled = self.log.wait_for(start, until, timeout or self.settle)
        time.sleep(self.quiet)
        end = self.end(mid)
        snap = self.snap(name, screenshot=screenshot, kinds=kinds) if snapshot else None
        return Step(mid, start, end, self.log.events(start, end), self.log.between(start, end), snap, settled)

    # -- input --------------------------------------------------------------------------------------------------------
    def tap(self, x: int, y: int) -> None:
        self.adb.shell("input tap %d %d" % (x, y))

    def key(self, code: int) -> None:
        self.adb.shell("input keyevent %d" % code)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 300) -> None:
        self.adb.shell("input swipe %d %d %d %d %d" % (x1, y1, x2, y2, ms))

    def type_token(self, token: str) -> None:
        if not re.fullmatch(r"[a-z0-9]+", token):
            raise ValueError("only plain tokens are typed by the collector")
        self.adb.shell("input text %s" % token)

    def type_secret(self, secret: str) -> None:
        """The PIN: never logged (adb command log redacted), never stored."""
        self.adb.shell("input text %s" % shlex.quote(secret), secret=True)

    @staticmethod
    def sentinel() -> str:
        return "dgs" + secrets.token_hex(4)

    # -- targets ------------------------------------------------------------------------------------------------------
    def home_targets(self) -> dict[str, Rect]:
        e = self.log.latest(lambda e: e.event == "targets" and e.act_class == "ProbeHomeActivity")
        if e is not None:
            return parsers.parse_targets(e)
        return self.home_targets_from_ui()

    def home_targets_from_ui(self) -> dict[str, Rect]:
        """Fallback when the probe home's targets event is no longer in the log buffer: needs the home focused."""
        remote = "/data/local/tmp/duogate-ui.xml"
        self.adb.shell("uiautomator dump %s" % remote, timeout=60)
        xml = self.adb.shell("cat %s" % remote)
        self.save_text("home-uiautomator", xml)
        out = {}
        for n in parsers.parse_uiautomator(xml):
            key = HOME_LABELS.get(n.text)
            if key is None or n.package != PROBE_PACKAGE:
                continue
            side = self.geo.side_of_point(*n.bounds.center())
            if side is None:
                continue
            name = "%s.%s" % (side, key)
            if name in out:
                raise EvidenceError("uiautomator: two %s buttons" % name)
            out[name] = n.bounds
        if not out:
            raise EvidenceError("uiautomator: no probe home buttons (is the probe home focused and visible?)")
        return out

    def task_targets(self, task_id: int) -> dict[str, Rect]:
        e = self.log.latest(lambda e: e.event == "targets" and e.task == task_id)
        if e is None:
            raise EvidenceError("no targets event from task %d (probe older than gate-2?)" % task_id)
        return parsers.parse_targets(e)

    def point(self, targets: dict[str, Rect], name: str, side: str | None = None,
              area: Rect | None = None) -> tuple[int, int]:
        """Centre of a logged control, refused unless it lies well inside the expected panel area (unrotated panel
        of `side`, or an explicit area such as a rotated panel read from the same dumps)."""
        r = targets.get(name)
        if r is None:
            raise EvidenceError("target %s unknown" % name)
        x, y = r.center()
        area = area or self.geo.panel(side)
        if not area.contains_point(x, y) or not (area.top + 60 < y < area.bottom - 80):
            raise EvidenceError("target %s at %s is not inside %s (scrolled or stale layout)" % (name, r, area))
        return x, y

    # -- tasks --------------------------------------------------------------------------------------------------------
    def probe_tasks(self) -> list[int]:
        c = parsers.parse_containers(self.adb.shell(DUMPS["containers"]))
        out = []
        for cls in TEST_CLASSES:
            for t in c.tasks_with_component("%s/.%s" % (PROBE_PACKAGE, cls)):
                if t not in out:
                    out.append(t)
        return out

    def remove_probe_tasks(self) -> None:
        """Removes the probe's test tasks (never the probe home); verified against the containers dump."""
        tasks = self.probe_tasks()
        for t in tasks:
            self.adb.shell("am stack remove %d" % t, check=False)
        if tasks:
            time.sleep(self.remove_wait)
            left = self.probe_tasks()
            if left:
                raise EvidenceError("cannot remove probe tasks %s (am stack remove); finish them by hand" % left)

    def home_component(self) -> str:
        return parsers.parse_resolved_home(self.adb.shell(
            "cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME"))

    def set_home(self, component: str) -> None:
        self.adb.shell("cmd package set-home-activity %s" % shlex.quote(component))
        got = self.home_component()
        if got.split("/")[0] != component.split("/")[0]:
            raise EvidenceError("HOME is %s after setting %s" % (got, component))

    def props(self) -> dict[str, str]:
        return parsers.parse_getprop(self.adb.shell("getprop"))

    def write_json(self, name: str, data) -> str:
        path = os.path.join(self.outdir, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        return path

    def close(self) -> None:
        self.log.close()


__all__ = ["Device", "Snapshot", "Step", "AdbError", "KEY_HOME", "KEY_BACK", "KEY_POWER", "KEY_ENTER", "KEY_WAKEUP"]
