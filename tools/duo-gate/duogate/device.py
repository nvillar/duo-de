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
SCROLL_SLOP = 24        # ScrollView touch slop (8 dp at 400 dpi = 20 px) plus a margin

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
        self.live = live
        self.remove_wait = 1.0
        self.boot_wait = 10.0
        self._home_cache: tuple[dict, int] | None = None
        self._ui_dumps = 0
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
        self._home_cache = None             # sequence numbers belong to the old capture; the home restarted anyway
        if not self.live:
            self._run_start = len(self.log.lines) - 1     # offline: keep the buffer, start a new run window
            return
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
    # Probe HOME events after which earlier positions are no longer trusted (a new targets event supersedes them).
    HOME_RELAYOUT = ("onCreate", "layout", "onConfigurationChanged", "onDestroy")

    def home_targets(self) -> dict[str, Rect]:
        """The probe home's control rectangles: its latest targets event, else positions found earlier in this run
        (targets event or uiautomator), as long as the home has not been laid out again since. The home logs targets
        only when its layout changes, so on a long-running home that event can be gone from the log buffer; the
        uiautomator fallback only sees the focused window, so it works right after Home but not once a probe task on
        the other panel has focus. Hence the cache, invalidated by any home relayout event."""
        e = self.log.latest(lambda e: e.event == "targets" and e.act_class == "ProbeHomeActivity")
        candidate = self._home_cache
        if e is not None and (candidate is None or e.seq >= candidate[1]):
            candidate = (parsers.parse_targets(e), e.seq)
        if candidate is not None:
            # whatever their source, positions older than a home relayout are stale
            moved = self.log.latest(lambda x: x.act_class == "ProbeHomeActivity" and x.event in self.HOME_RELAYOUT
                                    and x.seq > candidate[1])
            if moved is None:
                self._home_cache = candidate
                return candidate[0]
        self._home_cache = None
        targets = self.home_targets_from_ui()
        self._home_cache = (targets, len(self.log.lines) - 1)
        return targets

    def home_targets_from_ui(self) -> dict[str, Rect]:
        """Fallback when the probe home's targets event is no longer in the log buffer: needs the home focused.
        The raw hierarchy (which may hold unrelated on-screen text) is never stored: only the allowlisted probe
        control names and bounds are, and the device copy is deleted."""
        remote = "/data/local/tmp/duogate-ui.xml"
        try:
            self.adb.shell("uiautomator dump %s" % remote, timeout=60)
            xml = self.adb.shell("cat %s" % remote)
        finally:
            self.adb.shell("rm -f %s" % remote, check=False)
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
        nodes = parsers.parse_uiautomator(xml)
        del xml
        # what the dump showed, without any on-screen text: packages and probe classes of the root nodes
        seen = sorted({n.package for n in nodes})
        self._ui_dumps += 1
        path = self.save_text("home-targets-uiautomator-%d" % self._ui_dumps,
                              "".join("%s=%s\n" % (k, out[k]) for k in sorted(out))
                              or "(no probe home controls found; dumped window packages: %s, %d nodes)\n"
                              % (", ".join(seen), len(nodes)))
        if not out:
            raise EvidenceError("uiautomator: no probe home buttons in the focused window (packages %s; %s): the "
                                "home logged no targets in the log buffer and is not the focused window"
                                % (", ".join(seen), path))
        return out

    def task_targets(self, task_id: int) -> dict[str, Rect]:
        e = self.log.latest(lambda e: e.event == "targets" and e.task == task_id)
        if e is None:
            raise EvidenceError("no targets event from task %d (probe older than gate-2?)" % task_id)
        return parsers.parse_targets(e)

    def content_band(self, task_id: int, area: Rect) -> tuple[int, int]:
        """The vertical band of `area` where the task's content is visible and tappable: below its top bars (status +
        action bar) and above its bottom bar or the keyboard, from the task's latest insets event."""
        e = self.log.latest(lambda e: e.event == "insets" and e.task == task_id)
        top, bottom = 220, 80
        if e is not None:
            try:
                bars = [int(x) for x in e.kv.get("bars", "").strip("[]").split(",")]
                ime = [int(x) for x in e.kv.get("ime", "").strip("[]").split(",")]
                top, bottom = bars[1], max(bars[3], ime[3])
            except (ValueError, IndexError):
                raise EvidenceError("unparsable insets of task %d: %s" % (task_id, e.kv))
        return area.top + top, area.bottom - bottom

    def task_point(self, task_id: int, name: str, side: str | None = None, area: Rect | None = None,
                   attempts: int = 3) -> tuple[int, int]:
        """A tap point on a task's control that is really visible: inside the panel, below the bars and above the
        keyboard. A control scrolled away or covered by the keyboard is dragged into view first (a slow drag inside
        the visible band, so the ScrollView does not fling) and its new position is taken from the probe's targets
        event (DuoProbe gate-3 reports positions after a scroll; with gate-2 this is INCOMPLETE)."""
        area = area or self.geo.panel(side)
        for _ in range(attempts):
            targets = self.task_targets(task_id)
            r = targets.get(name)
            if r is None:
                raise EvidenceError("target %s unknown" % name)
            if not r.intersects(area):
                # stale or foreign bounds: never send input (tap or drag) outside the requested panel
                raise EvidenceError("target %s of task %d at %s is not on the requested panel %s" % (name, task_id, r,
                                                                                                   area))
            lo, hi = self.content_band(task_id, area)
            if hi - lo < 120:
                raise EvidenceError("no visible content band on task %d (%d..%d)" % (task_id, lo, hi))
            seen = Rect(max(r.left, area.left), max(r.top, lo + 10), min(r.right, area.right), min(r.bottom, hi - 10))
            if seen.height >= 40 and area.contains_point(*seen.center()):
                return seen.center()
            # scroll so that the control's centre lands in the middle of the band
            delta = (r.top + r.bottom) // 2 - (lo + hi) // 2
            x = (max(r.left, area.left) + min(r.right, area.right)) // 2
            dist = min(abs(delta), hi - lo - 80) + SCROLL_SLOP
            y0 = hi - 30 if delta > 0 else lo + 30
            y1 = y0 - dist if delta > 0 else y0 + dist
            for px, py in ((x, y0), (x, y1)):
                if not (area.left < px < area.right and lo <= py <= hi):
                    raise EvidenceError("refusing a scroll drag for %s of task %d: (%d,%d) is outside the visible "
                                        "content of %s (%d..%d)" % (name, task_id, px, py, area, lo, hi))
            before = self.log.latest(lambda e: e.event == "targets" and e.task == task_id)
            # below the fling threshold: ~80 px/s, at least 1 s
            st = self.step("scroll.%d.%s" % (task_id, name), lambda: self.swipe(x, y0, x, y1, max(1000, dist * 12)),
                           snapshot=False, until=lambda evs: any(
                               e.event == "targets" and e.task == task_id and e.seq > before.seq for e in evs))
            if not st.settled:
                raise EvidenceError("target %s of task %d at %s is outside the visible band %d..%d and the probe did "
                                    "not report a scrolled position (DuoProbe gate-3 needed)" % (name, task_id, r, lo, hi))
        raise EvidenceError("target %s of task %d not brought into view after %d scrolls" % (name, task_id, attempts))

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
