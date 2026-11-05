"""Offline device model for the tests: renders dumpsys texts in the r4 formats of the real captures (tests/fixtures)
and a FakeAdb that plays a two panel device driven by the collector's own adb commands.

Bugs can be injected (wrong panel routing, Home minimizing the focused instead of the gestured panel, missing events,
touches delivered to the wrong task) to prove that the collector reports FAIL or INCOMPLETE for them.
"""
from __future__ import annotations

import os
import re

from duogate.adb import Adb
from duogate.checks import DUO1
from duogate.model import Rect

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
PANEL = {"A": DUO1.panel("A"), "B": DUO1.panel("B")}
CLASS_OF = {"probeTest": "ProbeTestActivity", "probeTest2": "ProbeOtherTestActivity",
            "probeTestNew": "ProbeTestActivity", "probeLocked": "ProbeLockedTestActivity"}
LABEL_OF = {"probeTest": "Probe_test", "probeTest2": "Probe_test_2_(own_task)",
            "probeTestNew": "Probe_test_(new_instance)", "probeLocked": "Probe_locked_(showWhenLocked)"}
HOME_TASK = 19


def fixture(name: str) -> str:
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def r(rect: Rect) -> str:
    return str(rect)


class Task:
    def __init__(self, tid, cls, area, hidden=0, focused_editor=None):
        self.tid, self.cls, self.area, self.hidden = tid, cls, area, hidden
        self.act = "%s@%x" % (cls, 0x1000 + tid)
        self.stopped = False

    @property
    def bounds(self) -> Rect:
        return PANEL[self.area] if self.area in PANEL else DUO1.display

    @property
    def win(self) -> str:
        return "%07x" % (0xa000000 + self.tid)


# ---------------------------------------------------------------------------------------------------------------------
# dumpsys renderers (structure copied from tests/fixtures/panel-containers.txt and the off-* captures)

def containers(tasks: list[Task], ime_area: str | None = None, panels: bool = True) -> str:
    ime = PANEL[ime_area] if ime_area else Rect(0, 0, 2784, 1800)
    lines = ["ACTIVITY MANAGER CONTAINERS (dumpsys activity containers)",
             "└─ ROOT type=undefined mode=fullscreen override-mode=undefined requested-bounds=[0,0][0,0] "
             "bounds=[0,0][2784,1800]",
             "   └─ Display 0 name=\"Built-in Screen\" type=undefined mode=fullscreen override-mode=fullscreen "
             "requested-bounds=[0,0][2784,1800] bounds=[0,0][2784,1800]",
             "      └─ AppZoomOut:2:14 type=undefined mode=fullscreen override-mode=undefined "
             "requested-bounds=[0,0][0,0] bounds=[0,0][2784,1800]",
             "         ├─ ImePlaceholder:13:14 type=undefined mode=fullscreen override-mode=undefined "
             "requested-bounds=[0,0][0,0] bounds=[0,0][2784,1800]",
             "         │  └─ ImeContainer type=undefined mode=fullscreen override-mode=undefined "
             "requested-bounds=%s bounds=%s" % (r(ime), r(ime))]
    areas = (["Panel1", "Panel0"] if panels else []) + ["Default"]
    for a in areas:
        last = a == "Default"
        g = "└─" if last else "├─"
        pre = "            " if last else "         │  "
        if a == "Default":
            lines.append("         %s DefaultTaskDisplayArea type=undefined mode=fullscreen override-mode=fullscreen "
                         "requested-bounds=[0,0][0,0] bounds=[0,0][2784,1800]" % g)
            members = [t for t in tasks if t.area == "default"]
            home = ("%s├─ Task=1 type=HOME mode=fullscreen override-mode=undefined requested-bounds=[0,0][0,0] "
                    "bounds=[0,0][2784,1800]" % pre)
            lines.append(home if members else home.replace("├─", "└─"))
            hp = pre + ("│  " if members else "   ")
            lines.append("%s└─ Task=%d type=HOME mode=fullscreen override-mode=undefined requested-bounds=[0,0][0,0] "
                         "bounds=[0,0][2784,1800]" % (hp, HOME_TASK))
            lines.append("%s   └─ ActivityRecord{207969393 u0 org.duode.probe/.ProbeHomeActivity t%d} type=HOME "
                         "mode=fullscreen override-mode=undefined requested-bounds=[0,0][0,0] bounds=[0,0][2784,1800]"
                         % (hp, HOME_TASK))
            _tasks(lines, members, pre)
            continue
        side = "A" if a == "Panel0" else "B"
        b = PANEL[side]
        lines.append("         %s %s type=undefined mode=fullscreen override-mode=undefined requested-bounds=%s "
                     "bounds=%s" % (g, a, r(b), r(b)))
        lines.append("%s└─ %sTasks type=undefined mode=fullscreen override-mode=undefined requested-bounds=[0,0][0,0] "
                     "bounds=%s" % (pre, a, r(b)))
        _tasks(lines, [t for t in tasks if t.area == side], pre + "   ")
    return "\n".join(lines) + "\n"


def _tasks(lines, members, pre):
    for i, t in enumerate(members):
        last = i == len(members) - 1
        g = "└─" if last else "├─"
        sub = pre + ("   " if last else "│  ")
        lines.append("%s%s Task=%d type=standard mode=fullscreen override-mode=undefined requested-bounds=[0,0][0,0] "
                     "bounds=%s" % (pre, g, t.tid, r(t.bounds)))
        lines.append("%s└─ ActivityRecord{%d u0 org.duode.probe/.%s t%d} type=standard mode=fullscreen "
                     "override-mode=undefined requested-bounds=[0,0][0,0] bounds=%s"
                     % (sub, 100000 + t.tid, t.cls, t.tid, r(t.bounds)))


def activities(tasks: list[Task], keyguard: bool = False, occluded: bool = False, raw_hidden: dict | None = None) -> str:
    out = ["ACTIVITY MANAGER ACTIVITIES (dumpsys activity activities)", "Display #0 (activities from top to bottom):"]
    for t in tasks:
        visible = "false" if (t.hidden & 8 or t.stopped or keyguard) else "true"
        out.append("  * Task{%x #%d type=standard A=10190:org.duode.probe U=0 visible=%s visibleRequested=%s "
                   "mode=fullscreen translucent=false sz=1}" % (0xabc00 + t.tid, t.tid, visible, visible))
        if t.hidden or (raw_hidden and t.tid in raw_hidden):
            out.append("    mForceHiddenFlags=%s" % ((raw_hidden or {}).get(t.tid, t.hidden)))
        out.append("    * Hist  #0: ActivityRecord{%d u0 org.duode.probe/.%s t%d}" % (100000 + t.tid, t.cls, t.tid))
        out.append("      Intent { act=android.intent.action.MAIN cat=[android.intent.category.LAUNCHER] "
                   "flg=0x10200000 cmp=org.duode.probe/.%s bnds=[40,421][1310,581] }" % t.cls)
        state = "STOPPED" if visible == "false" else "RESUMED"
        out.append("      state=%s delayedResume=false finishing=false" % state)
        out.append("")
    out.append("  * Task{b9e9d86 #1 type=home U=0 visible=true visibleRequested=true mode=fullscreen translucent=false "
               "sz=1}")
    out.append("    * Task{adbcb4d #%d type=home I=org.duode.probe/.ProbeHomeActivity U=0 rootTaskId=1 visible=true "
               "visibleRequested=true mode=fullscreen translucent=false sz=1}" % HOME_TASK)
    out.append("      * Hist  #0: ActivityRecord{207969393 u0 org.duode.probe/.ProbeHomeActivity t%d}" % HOME_TASK)
    out.append("        state=RESUMED delayedResume=false finishing=false")
    out.append("")
    out.append("  Resumed activities in task display areas (from top to bottom):")
    for t in tasks:
        if not (t.hidden & 8 or t.stopped or keyguard):
            out.append("    Resumed: ActivityRecord{%d u0 org.duode.probe/.%s t%d}" % (100000 + t.tid, t.cls, t.tid))
    out.append("")
    out.append("  KeyguardController:")
    out.append("    mKeyguardShowing=%s" % str(keyguard).lower())
    out.append("   KeyguardShowing=%s AodShowing=false KeyguardGoingAway=false DismissalRequested=false  Occluded=%s "
               "DismissingKeyguardActivity=null TurnScreenOnActivity=null at display=0"
               % (str(keyguard).lower(), str(occluded).lower()))
    return "\n".join(out) + "\n"


def windows(tasks: list[Task], keyguard: bool = False, leak: int | None = None) -> str:
    out = ["WINDOW MANAGER WINDOWS (dumpsys window windows)",
           "  Window #0 Window{671cb1b u0 NotificationShade}:",
           "    mDisplayId=0 mSession=Session{de0912e 4003:u0a10178} mClient=android.os.BinderProxy@1417a15",
           "    mOwnerUid=10178 showForAllUsers=true package=com.android.systemui appop=NONE",
           "    Frames: parent=[0,0][2784,1800] display=[0,0][2784,1800] frame=[0,0][2784,1800] "
           "last=[0,0][2784,1800] insetsChanged=false",
           "    isOnScreen=%s" % str(keyguard).lower(), "    isVisible=%s" % str(keyguard).lower()]
    for i, t in enumerate(tasks):
        on = not (t.hidden & 8 or t.stopped) and (not keyguard or t.tid == leak)
        out += ["  Window #%d Window{%s u0 org.duode.probe/org.duode.probe.%s}:" % (i + 1, t.win, t.cls),
                "    mDisplayId=0 taskId=%d mSession=Session{1 5000:u0a10190} mClient=android.os.BinderProxy@1"
                % t.tid,
                "    mOwnerUid=10190 showForAllUsers=false package=org.duode.probe appop=NONE",
                "    Frames: parent=%s display=[0,0][2784,1800] frame=%s last=%s insetsChanged=false"
                % (r(t.bounds), r(t.bounds), r(t.bounds)),
                "    isOnScreen=%s" % str(on).lower(), "    isVisible=%s" % str(on).lower()]
    out += ["", "  mGlobalConfiguration={1.0 ?mcc0mnc}", "  mTopFocusedDisplayId=0"]
    return "\n".join(out) + "\n"


def _w(t: Task | None) -> str:
    return "Window{%s u0 org.duode.probe/org.duode.probe.%s}" % (t.win, t.cls) if t else "null"


def wdisplays(focus: Task | None, ime: Task | None = None, keyguard: bool = False, rotation: int = 0) -> str:
    cur = "Window{671cb1b u0 NotificationShade}" if keyguard else _w(focus)
    app = ("ActivityRecord{%d u0 org.duode.probe/.%s t%d}" % (100000 + focus.tid, focus.cls, focus.tid)
           if focus else "null")
    return "\n".join([
        "WINDOW MANAGER DISPLAY CONTENTS (dumpsys window displays)",
        "  Display: mDisplayId=0 (organized)",
        "    init=2784x1800 400dpi mMinSizeOfResizeableTaskDp=220 cur=2784x1800 app=2784x1800",
        "  overrideConfig={1.0 ?mcc0mnc [en_US] winConfig={ mBounds=Rect(0, 0 - 2784, 1800) "
        "mDisplayRotation=ROTATION_%d mWindowingMode=fullscreen} s.75}" % (rotation * 90),
        "  mImeLayeringTarget=%s" % _w(ime), "  mImeInputTarget=%s" % _w(ime), "  mImeControlTarget=%s" % _w(ime),
        "  mCurrentFocus=%s" % cur, "  mFocusedApp=%s" % app, "  mFixedRotationLaunchingApp=null", ""])


def inputdump(tasks: list[Task], focus: Task | None, keyguard: bool = False, touchable_leak: int | None = None) -> str:
    fw = "671cb1b NotificationShade" if keyguard else (
        "%s org.duode.probe/org.duode.probe.%s" % (focus.win, focus.cls) if focus else "")
    out = ["INPUT MANAGER (dumpsys input)", "Input Dispatcher State:", "  FocusedDisplayId: 0",
           "  FocusedApplications:"]
    if focus:
        out.append("    displayId=0, name='ActivityRecord{%d u0 org.duode.probe/.%s t%d}', dispatchingTimeout=5000ms"
                   % (100000 + focus.tid, focus.cls, focus.tid))
    out += ["  FocusedWindows:", "    displayId=0, name='%s'" % fw, "  FocusRequests:", "  Display: 0",
            "    Windows:",
            "      0: name=671cb1b NotificationShade, id=595, displayId=0, inputConfig=%s, alpha=1, "
            "frame=[0,0][2784,1800], globalScale=1, applicationInfo.name=, applicationInfo.token=<null>, "
            "touchableRegion=[0,0][2784,1800], ownerPid=4003, ownerUid=10178"
            % ("TRUSTED_OVERLAY" if keyguard else "NOT_TOUCHABLE | TRUSTED_OVERLAY")]
    for i, t in enumerate(tasks):
        hidden = (t.hidden & 8 or t.stopped or keyguard) and t.tid != touchable_leak
        out.append("      %d: name=%s org.duode.probe/org.duode.probe.%s, id=%d, displayId=0, inputConfig=%s, alpha=1, "
                   "frame=%s, globalScale=1, applicationInfo.name=, applicationInfo.token=<null>, "
                   "touchableRegion=%s, ownerPid=5000, ownerUid=10190"
                   % (i + 1, t.win, t.cls, 900 + i, "NOT_VISIBLE | NOT_TOUCHABLE" if hidden else "DUPLICATE_TOUCH_TO_WALLPAPER",
                      r(t.bounds), r(t.bounds)))
    return "\n".join(out) + "\n"


class Snap:
    """Snapshot stand-in built from texts (same parsing as device.Snapshot)."""

    def __new__(cls, **texts):
        from duogate.device import Snapshot
        return Snapshot("test", texts)


def snap_for(tasks, focus=None, ime=None, ime_area=None, keyguard=False, occluded=False, leak=None, panels=True,
             touchable_leak=None):
    return Snap(containers=containers(tasks, ime_area, panels), activities=activities(tasks, keyguard, occluded),
                windows=windows(tasks, keyguard, leak), wdisplays=wdisplays(focus, ime, keyguard),
                input=inputdump(tasks, focus, keyguard, touchable_leak))


# ---------------------------------------------------------------------------------------------------------------------
# log lines

class Clock:
    def __init__(self):
        self.t = 0

    def stamp(self) -> str:
        self.t += 7
        s = self.t
        return "11-05 10:%02d:%02d.%03d" % (s // 60000 % 60, s // 1000 % 60, s % 1000)


CLOCK = Clock()


def line(tag: str, msg: str, level: str = "I", pid: int = 5000) -> str:
    return "%s %5d %5d %s %-8s: %s" % (CLOCK.stamp(), pid, pid, level, tag, msg)


def probe(event: str, task: Task | int, rest: str = "", act: str | None = None) -> str:
    if isinstance(task, Task):
        act, tid = act or task.act, task.tid
    else:
        tid = task
    return line("DuoProbe", "event=%s act=%s task=%d%s" % (event, act, tid, (" " + rest) if rest else ""))


def resume(task: Task, bounds: Rect | None = None) -> str:
    b = bounds or task.bounds
    return probe("onResume", task, "display=0 rotation=0 bounds=%s maxBounds=%s multiWindow=false "
                 "orientation=portrait sizeDp=540x720 swDp=540 dpi=400 requestedOrientation=-1 keyguardLocked=false"
                 % (r(b), r(b)))


def launch(side: str, key: str, display_id: str = "unset") -> str:
    return probe("launch", HOME_TASK, "side=%s label=%s component=org.duode.probe/.%s action=android.intent.action.MAIN "
                 "categories={android.intent.category.LAUNCHER} flags=0x10200000 sourceBounds=[40,421][1310,581] "
                 "launchDisplayId=%s windowingMode=unset" % (side, LABEL_OF[key], CLASS_OF[key], display_id),
                 act="ProbeHomeActivity@77")


def targets_for(task: Task) -> str:
    b = task.bounds
    return probe("targets", task, "title=[%d,260][%d,341] editor1=[%d,798][%d,911] editor2=[%d,911][%d,1130] "
                 "showIme=[%d,1130][%d,1270] hideIme=[%d,1270][%d,1410]"
                 % ((b.left + 40, b.right - 40) * 5))


def home_targets() -> str:
    parts = []
    for side, x0, x1 in (("A", 40, 1310), ("B", 1474, 2744)):
        y = 341
        for key in ("toggle", "probeTest", "probeTest2", "probeTestNew", "probeLocked"):
            h = 80 if key == "toggle" else 160
            parts.append("%s.%s=[%d,%d][%d,%d]" % (side, key, x0, y, x1, y + h))
            y += h
    return probe("targets", HOME_TASK, " ".join(parts), act="ProbeHomeActivity@77")


def marker(mid: str, step: str = "x") -> str:
    return line("DuoGate", "mark id=%s step=%s" % (mid, step), pid=6000)


# ---------------------------------------------------------------------------------------------------------------------
# a fake two panel device behind FakeAdb

class Sim:
    """Minimal behaviour of the two panel prototype as the collector sees it. bugs: set of names."""

    def __init__(self, log, bugs=()):
        self.log = log
        self.bugs = set(bugs)
        self.tasks: list[Task] = []
        self.next_tid = 20
        self.focus: Task | None = None
        self.toggle = False
        self.commands: list[str] = []
        self.settings = {("secure", "navigation_mode"): "2", ("secure", "show_ime_with_hard_keyboard"): "0",
                         ("system", "user_rotation"): "0"}
        self.props = {"sys.duode.panels.active": "1", "persist.debug.phh.duo.panels": "1",
                      "ro.build.display.id": "treble_arm64_bvN-userdebug 16 SIM", "ro.duode.dev_build": "true"}
        self.home_component = "com.android.launcher3/.uioverrides.QuickstepLauncher"
        self.credential = "NONE"
        self.ui_xml = fixture("probe-home-uiautomator.xml")
        self.props["sys.boot_completed"] = "1"
        self.rotation = 0
        self.reboots = 0
        self.root = False
        self.emit(home_targets())

    def emit(self, raw: str) -> None:
        self.log.feed(raw)

    def reboot(self):
        self.reboots += 1
        self.tasks = []
        on = "1" in (self.props.get("persist.sys.phh.duo.panels"), self.props.get("persist.debug.phh.duo.panels"))
        self.props["sys.duode.panels.active"] = "1" if on else "0"
        self.props["sys.boot_completed"] = "1"

    def task_at(self, side):
        live = [t for t in self.tasks if t.area == side and not t.hidden & 8 and not t.stopped]
        return live[-1] if live else None

    def home_button(self, x, y):
        for side, x0, x1 in (("A", 40, 1310), ("B", 1474, 2744)):
            if x0 <= x < x1:
                yy = 341
                for key in ("toggle", "probeTest", "probeTest2", "probeTestNew", "probeLocked"):
                    h = 80 if key == "toggle" else 160
                    if yy <= y < yy + h:
                        return side, key
                    yy += h
        return None, None

    def tap(self, x, y):
        side = "A" if x < 1350 else ("B" if x >= 1434 else None)
        top = self.task_at(side) if side else None
        if top is not None:
            receiver = top
            if "touch_to_other" in self.bugs:
                receiver = self.task_at("B" if side == "A" else "A") or top
            if "no_touch_log" not in self.bugs:
                self.emit(probe("touch", receiver, "action=down x=10 y=10 rawX=%d rawY=%d pointers=1" % (x, y)))
                self.emit(probe("touch", receiver, "action=up x=10 y=10 rawX=%d rawY=%d pointers=1" % (x, y)))
            self.focus = receiver
            return
        bside, key = self.home_button(x, y)
        if key == "toggle":
            self.toggle = not self.toggle
            self.emit(probe("toggle", HOME_TASK, "sendLaunchDisplayId=%s" % str(self.toggle).lower(),
                            act="ProbeHomeActivity@77"))
            return
        if key is None:
            return
        cls = CLASS_OF[key]
        self.emit(launch(bside, key, "0" if self.toggle else "unset"))
        if "no_resume" in self.bugs:
            return
        dest = bside
        if "wrong_panel" in self.bugs:
            dest = "B" if bside == "A" else "A"
        existing = [t for t in self.tasks if t.cls == cls] if key != "probeTestNew" else []
        if existing and "no_reuse" not in self.bugs:
            t = existing[0]
            t.area, t.hidden, t.stopped = dest, 0, False
            self.tasks.remove(t)
            self.tasks.append(t)
            self.emit(line("DuoDisplayAreaPolicy", "Restored minimized Task{abc #%d type=standard}" % t.tid, pid=1000))
            fresh = False
        else:
            t = Task(self.next_tid, cls, dest)
            self.next_tid += 1
            self.tasks.append(t)
            fresh = True
        self.emit(resume(t))
        if fresh:
            self.emit(targets_for(t))
        self.focus = t

    def home(self, gesture_side=None):
        side = gesture_side
        if side is None or "home_focused_panel" in self.bugs:
            side = self.focus.area if self.focus is not None else None
        if side not in PANEL:
            return
        victims = [t for t in self.tasks if t.area == side and not t.hidden & 8]
        self.emit(line("DuoDisplayAreaPolicy", "Home: minimizing Panel%d (%d tasks)" % ("AB".index(side),
                                                                                         len(victims)), pid=1000))
        for t in victims:
            t.hidden |= 8
            self.emit(probe("onPause", t))
            self.emit(probe("onStop", t))
        if "home_stops_other" in self.bugs:
            o = self.task_at("B" if side == "A" else "A")
            if o:
                self.emit(probe("onStop", o))
        self.focus = None

    def back(self, side):
        t = self.task_at(side)
        if t is None:
            return
        if "back_to_focused" in self.bugs and self.focus is not None:
            t = self.focus
        t.stopped = True
        self.emit(probe("onPause", t))
        self.emit(probe("onStop", t))

    def shell(self, cmd: str) -> str:
        self.commands.append(cmd)
        m = re.match(r"log -t DuoGate '?mark id=(\S+) step=([^']*)'?", cmd)
        if m:
            self.emit(marker(m.group(1), m.group(2)))
            return ""
        m = re.match(r"input tap (\d+) (\d+)", cmd)
        if m:
            self.tap(int(m.group(1)), int(m.group(2)))
            return ""
        if cmd == "input keyevent 3":
            self.home()
            return ""
        if cmd.startswith("input text "):
            return ""          # the simulated probe has no editors: no text event (input not received)
        m = re.match(r"input swipe (\d+) (\d+) (\d+) (\d+)", cmd)
        if m:
            x1, y1, x2, y2 = (int(g) for g in m.groups())
            side = "A" if x1 < 1392 else "B"
            if y1 > 1700 and y2 < y1:
                if "synthetic_home_ignored" not in self.bugs:
                    self.home(side)
            elif abs(x2 - x1) > 300:
                self.back(side)
            return ""
        m = re.match(r"am stack remove (\d+)", cmd)
        if m:
            self.tasks = [t for t in self.tasks if t.tid != int(m.group(1))]
            return ""
        m = re.match(r"am start (--display 0|--windowingMode 5) -n org.duode.probe/.ProbeTestActivity", cmd)
        if m:
            t = Task(self.next_tid, "ProbeTestActivity", "default")
            self.next_tid += 1
            self.tasks.append(t)
            self.emit(resume(t))
            return ""
        focus = self.focus if self.focus in self.tasks else None
        m = re.match(r"settings (get|put|delete) (\w+) (\S+)(?: (\S+))?$", cmd)
        if m:
            op, ns, key, value = m.groups()
            key = key.strip("'")
            if op == "get":
                return "%s\n" % self.settings.get((ns, key), "null")
            if op == "put":
                self.settings[(ns, key)] = value.strip("'")
                if key == "user_rotation":
                    if "rotation_ignored" in self.bugs:
                        return ""
                    self.rotation = int(value)
                    for t in self.tasks:
                        self.emit(probe("onConfigurationChanged", t, "display=0 rotation=%s" % value))
                        self.emit(targets_for(t))
            else:
                self.settings.pop((ns, key), None)
            return ""
        if cmd == "getprop":
            return "".join("[%s]: [%s]\n" % kv for kv in sorted(self.props.items()))
        if cmd.startswith("getprop "):
            return self.props.get(cmd.split()[1].strip("'"), "") + "\n"
        m = re.match(r"cmd package set-home-activity (\S+)", cmd)
        if m:
            self.home_component = m.group(1).strip("'")
            return "Success\n"
        if cmd.startswith("cmd package resolve-activity"):
            return "priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=true\n%s\n" % \
                self.home_component
        simple = {
            "date '+%Y-%m-%d %H:%M:%S'": "2026-11-05 10:00:00\n",
            "dumpsys package org.duode.probe": "    versionCode=36 minSdk=36 targetSdk=36\n    versionName=gate-2\n"
                                               "    codePath=/system/app/DuoProbe\n",
            "dumpsys activity recents": "  mRecentsComponent=ComponentInfo{com.android.launcher3/"
                                        "com.android.quickstep.RecentsActivity}\n",
            "wm density": "Physical density: 400\n",
            "wm size": "Physical size: 2784x1800\n",
            "dumpsys display": "    mDisplayOffset=(0, 0)\n",
            "dumpsys device_state": fixture("device-state.txt"),
            "dumpsys lock_settings": "User State:\n  User 0\n   CredentialType: %s\n" % self.credential,
        }
        if cmd in simple:
            return simple[cmd]
        if cmd.startswith("sha256sum ") or cmd.startswith("pm path "):
            return "0123abcd  %s\n" % cmd.split()[-1]
        if cmd.startswith("dumpsys dropbox --print"):
            return "Drop box contents: 0 entries\nMax entries: 1000\n"
        if cmd.startswith("rm -f"):
            return ""
        m = re.match(r"setprop (\S+) (\S+)$", cmd)
        if m:
            if not m.group(1).startswith("persist.sys.") or self.root:
                self.props[m.group(1)] = m.group(2)
            return ""
        if cmd.startswith("uiautomator dump"):
            return "UI hierchary dumped to: /data/local/tmp/duogate-ui.xml\n"
        if cmd == "cat /data/local/tmp/duogate-ui.xml":
            return self.ui_xml
        if cmd == "dumpsys activity containers":
            return containers(self.tasks)
        if cmd == "dumpsys activity activities":
            return activities(self.tasks)
        if cmd == "dumpsys window windows":
            return windows(self.tasks)
        if cmd == "dumpsys window displays":
            return wdisplays(focus, rotation=self.rotation)
        if cmd == "dumpsys input":
            return inputdump(self.tasks, focus)
        if cmd.startswith("screencap"):
            return ""
        raise AssertionError("unexpected adb shell command %r" % cmd)


class FakeAdb(Adb):
    """The real Adb helpers (setting, getprop, screenshot...) over the simulated device instead of a subprocess."""

    def __init__(self, sim: Sim | None = None, lost_after: int | None = None):
        super().__init__(None, "adb-fake")
        self.sim = sim
        self.lost_after = lost_after
        self.calls = 0
        self.log_lines: list[str] = []
        self._log = self.log_lines.append

    def run(self, args, timeout=None, secret=False, check=True, binary=False):
        from duogate.adb import AdbError
        self.calls += 1
        self._log("adb %s" % (" ".join(args) if not secret else args[0] + " <redacted>"))
        if self.lost_after is not None and self.calls > self.lost_after:
            raise AdbError("device lost during adb shell: error: device 'x' not found")
        if args[0] == "shell":
            return self.sim.shell(args[1])
        if args[0] == "get-serialno":
            return "SIMULATED\n"
        if args[0] == "pull":
            return ""
        if args[0] == "reboot":
            self.sim.reboot()
            return ""
        if args[0] == "wait-for-device":
            return ""
        raise AssertionError("unexpected adb %r" % args)
