"""Parsers for the device evidence the collector relies on.

Every parser is strict about the structure it depends on and raises EvidenceError (-> INCOMPLETE) when that structure
is missing or does not match, instead of returning an empty or partial result that could read as a pass. The formats
are those of Android 16 r4 on the Surface Duo 1 gate image; the fixtures under tests/fixtures are real captures.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .model import EvidenceError, Rect

_RECT = r"\[-?\d+,-?\d+\]\[-?\d+,-?\d+\]"
_TREE_GLYPHS = " │├└─"


# ---------------------------------------------------------------------------------------------------------------------
# dumpsys activity containers / dumpsys window containers

@dataclass
class Node:
    name: str
    type: str
    mode: str
    override_mode: str | None
    requested_bounds: Rect | None
    bounds: Rect
    column: int
    parent: "Node | None" = None
    children: list["Node"] = field(default_factory=list)

    @property
    def task_id(self) -> int | None:
        m = re.match(r"Task=(\d+)", self.name)
        return int(m.group(1)) if m else None

    @property
    def activity(self) -> tuple[str, int] | None:
        """(component, task id) of an ActivityRecord node."""
        m = re.match(r"ActivityRecord\{\S+ u\d+ (\S+) t(-?\d+)\}", self.name)
        return (m.group(1), int(m.group(2))) if m else None

    @property
    def is_task_display_area(self) -> bool:
        return (self.name == "DefaultTaskDisplayArea" or re.fullmatch(r"Panel\d+Tasks", self.name) is not None
                or self.name.endswith("TaskDisplayArea"))

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def ancestors(self):
        n = self.parent
        while n is not None:
            yield n
            n = n.parent


_NODE = re.compile(r"^(?P<name>.*?) type=(?P<type>\S+) mode=(?P<mode>\S+)"
                   r"(?: override-mode=(?P<omode>\S+))?(?: requested-bounds=(?P<req>" + _RECT + r"))?"
                   r" bounds=(?P<bounds>" + _RECT + r")\s*$")


class Containers:
    def __init__(self, root: Node):
        self.root = root
        self.nodes = list(root.walk())

    def displays(self) -> list[Node]:
        return [n for n in self.nodes if re.match(r"Display \d+\b", n.name)]

    def find(self, name: str) -> Node | None:
        hits = [n for n in self.nodes if n.name == name]
        if len(hits) > 1:
            raise EvidenceError("containers: %d nodes named %s" % (len(hits), name))
        return hits[0] if hits else None

    def task(self, task_id: int) -> Node | None:
        hits = [n for n in self.nodes if n.task_id == task_id]
        if len(hits) > 1:
            raise EvidenceError("containers: task %d listed %d times" % (task_id, len(hits)))
        return hits[0] if hits else None

    def task_display_area(self, node: Node) -> Node | None:
        for a in node.ancestors():
            if a.is_task_display_area:
                return a
        return None

    def panel_names(self) -> list[str]:
        return sorted(n.name for n in self.nodes if re.fullmatch(r"Panel\d+(Tasks)?", n.name))

    def ime_container(self) -> Node | None:
        return self.find("ImeContainer")

    def tasks_with_component(self, component_prefix: str) -> list[int]:
        out = []
        for n in self.nodes:
            a = n.activity
            if a and a[0].startswith(component_prefix) and a[1] not in out:
                out.append(a[1])
        return out

    def leaf_tasks_in(self, area: Node) -> list[int]:
        """Ids of tasks that hold activities, below a task display area."""
        out = []
        for n in area.walk():
            a = n.activity
            if a and a[1] not in out:
                out.append(a[1])
        return out


def parse_containers(text: str) -> Containers:
    root = None
    stack: list[Node] = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("ACTIVITY MANAGER") or line.startswith("WINDOW MANAGER"):
            continue
        if "─ " not in line and root is not None:
            raise EvidenceError("containers: unexpected line %r" % line[:120])
        col = len(line) - len(line.lstrip(_TREE_GLYPHS))
        m = _NODE.match(line[col:])
        if not m:
            raise EvidenceError("containers: unparsable node %r" % line[:160])
        node = Node(m.group("name"), m.group("type"), m.group("mode"), m.group("omode"),
                    Rect.parse(m.group("req")) if m.group("req") else None, Rect.parse(m.group("bounds")), col)
        while stack and stack[-1].column >= col:
            stack.pop()
        if stack:
            node.parent = stack[-1]
            stack[-1].children.append(node)
        elif root is None:
            root = node
        else:
            raise EvidenceError("containers: more than one root")
        stack.append(node)
    if root is None or root.name != "ROOT":
        raise EvidenceError("containers: no ROOT node")
    c = Containers(root)
    if not c.displays():
        raise EvidenceError("containers: no display node")
    return c


# ---------------------------------------------------------------------------------------------------------------------
# dumpsys activity activities

@dataclass
class ActivityInfo:
    record: str
    component: str
    task_id: int
    state: str | None = None
    intent: str | None = None

    @property
    def source_bounds(self) -> Rect | None:
        m = re.search(r"\bbnds=(" + _RECT + ")", self.intent or "")
        return Rect.parse(m.group(1)) if m else None


@dataclass
class TaskInfo:
    task_id: int
    header: str
    visible: bool | None
    mode: str | None
    type: str | None
    force_hidden: int = 0
    force_hidden_raw: str | None = None
    activities: list[ActivityInfo] = field(default_factory=list)


@dataclass
class Activities:
    tasks: dict[int, TaskInfo]
    resumed_task_ids: list[int]
    keyguard_showing: bool | None
    keyguard_occluded: bool | None
    aod_showing: bool | None

    def task(self, task_id: int) -> TaskInfo | None:
        return self.tasks.get(task_id)


def _force_hidden(value: str) -> int:
    """mForceHiddenFlags as TaskFragment.dump prints it (decimal int, r4)."""
    v = value.strip()
    if not v.isdigit():
        raise EvidenceError("mForceHiddenFlags=%s is not a decimal number" % value)
    return int(v)


_TASK_HEADER = re.compile(r"^\s*\* Task\{[0-9a-f]+ #(\d+) (.*)\}\s*$")
_HIST = re.compile(r"^\s*\* Hist\s+#\d+: (ActivityRecord\{\S+ u\d+ (\S+) t(-?\d+)(?: f)?\}\}?)")


def parse_activities(text: str) -> Activities:
    if "ACTIVITY MANAGER ACTIVITIES" not in text and "Display #" not in text:
        raise EvidenceError("activities: not a dumpsys activity activities output")
    tasks: dict[int, TaskInfo] = {}
    current: TaskInfo | None = None
    act: ActivityInfo | None = None
    resumed: list[int] = []
    kg_showing = kg_occluded = aod = None
    for line in text.splitlines():
        m = _TASK_HEADER.match(line)
        if m:
            tid = int(m.group(1))
            rest = m.group(2)
            kv = dict(re.findall(r"(\w+)=(\S+)", rest))
            if tid not in tasks:
                tasks[tid] = TaskInfo(tid, line.strip(), {"true": True, "false": False}.get(kv.get("visible")),
                                      kv.get("mode"), kv.get("type"))
            current, act = tasks[tid], None
            continue
        m = re.search(r"\bmForceHiddenFlags=(\S+)", line)
        if m and current is not None:
            current.force_hidden_raw = m.group(1)
            current.force_hidden = _force_hidden(m.group(1))
            continue
        m = _HIST.match(line)
        if m and current is not None:
            act = ActivityInfo(m.group(1), m.group(2), int(m.group(3)))
            current.activities.append(act)
            continue
        s = line.strip()
        if act is not None and s.startswith("Intent {"):
            act.intent = s
        elif act is not None and s.startswith("state=") and act.state is None:
            act.state = s.split()[0].split("=", 1)[1]
        m = re.match(r"\s*Resumed: ActivityRecord\{\S+ u\d+ \S+ t(-?\d+)\}", line)
        if m:
            resumed.append(int(m.group(1)))
        m = re.search(r"\bKeyguardShowing=(true|false) AodShowing=(true|false).*?\bOccluded=(true|false).*"
                      r" at display=0\b", line)
        if m:
            kg_showing, aod, kg_occluded = (m.group(i) == "true" for i in (1, 2, 3))
    if not tasks:
        raise EvidenceError("activities: no tasks listed")
    return Activities(tasks, resumed, kg_showing, kg_occluded, aod)


# ---------------------------------------------------------------------------------------------------------------------
# dumpsys window displays / windows

_WINREF = re.compile(r"Window\{([0-9a-f]+) u\d+ ([^}]*)\}")


@dataclass
class WindowRef:
    hash: str
    title: str


def _winref(value: str) -> WindowRef | None:
    m = _WINREF.search(value)
    return WindowRef(m.group(1), m.group(2)) if m else None


@dataclass
class DisplayFocus:
    current_focus: WindowRef | None
    focused_app_task: int | None
    focused_app: str | None
    ime_layering_target: WindowRef | None
    ime_input_target: WindowRef | None
    ime_control_target: WindowRef | None
    rotation: int | None = None      # degrees, from the display's own configuration (mDisplayRotation)


def parse_window_displays(text: str, display_id: int = 0) -> DisplayFocus:
    parts = re.split(r"(?m)^\s*Display: mDisplayId=(\d+)", text)
    block = None
    for i in range(1, len(parts) - 1, 2):
        if int(parts[i]) == display_id:
            block = parts[i + 1]
            break
    if block is None:
        raise EvidenceError("window displays: display %d not found" % display_id)

    def field_value(name):
        m = re.search(r"(?m)^\s*" + name + r"=(.*)$", block)
        if not m:
            raise EvidenceError("window displays: %s missing" % name)
        return m.group(1).strip()

    focus = field_value("mCurrentFocus")
    app = field_value("mFocusedApp")
    m = re.search(r"ActivityRecord\{\S+ u\d+ (\S+) t(-?\d+)\}", app)
    r = re.search(r"mDisplayRotation=ROTATION_(\d+)", block)
    return DisplayFocus(_winref(focus), int(m.group(2)) if m else None, m.group(1) if m else None,
                        _winref(field_value("mImeLayeringTarget")), _winref(field_value("mImeInputTarget")),
                        _winref(field_value("mImeControlTarget")), int(r.group(1)) if r else None)


@dataclass
class WindowInfo:
    hash: str
    title: str
    display_id: int | None = None
    task_id: int | None = None
    package: str | None = None
    on_screen: bool | None = None
    visible: bool | None = None
    has_surface: bool | None = None
    frame: Rect | None = None


def parse_window_windows(text: str) -> dict[str, WindowInfo]:
    wins: dict[str, WindowInfo] = {}
    cur: WindowInfo | None = None
    for line in text.splitlines():
        m = re.match(r"^\s*Window #\d+ Window\{([0-9a-f]+) u\d+ ([^}]*)\}:\s*$", line)
        if m:
            cur = WindowInfo(m.group(1), m.group(2))
            wins[cur.hash] = cur
            continue
        if cur is None:
            continue
        m = re.match(r"^\s*mDisplayId=(\d+) taskId=(-?\d+)", line)
        if m:
            cur.display_id, cur.task_id = int(m.group(1)), int(m.group(2))
            continue
        m = re.match(r"^\s*mDisplayId=(\d+) ", line)
        if m:
            cur.display_id = int(m.group(1))
            continue
        m = re.match(r"^\s*mOwnerUid=\d+ .*\bpackage=(\S+)", line)
        if m:
            cur.package = m.group(1)
            continue
        m = re.match(r"^\s*mHasSurface=(true|false)", line)
        if m:
            cur.has_surface = m.group(1) == "true"
        m = re.match(r"^\s*Frames: .*? frame=(" + _RECT + ")", line)
        if m:
            cur.frame = Rect.parse(m.group(1))
        m = re.match(r"^\s*isOnScreen=(true|false)\s*$", line)
        if m:
            cur.on_screen = m.group(1) == "true"
        m = re.match(r"^\s*isVisible=(true|false)\s*$", line)
        if m:
            cur.visible = m.group(1) == "true"
        if re.match(r"^\s*mGlobalConfiguration=", line):
            cur = None
    if not wins:
        raise EvidenceError("window windows: no windows listed")
    return wins


# ---------------------------------------------------------------------------------------------------------------------
# dumpsys input

@dataclass
class InputWindow:
    name: str
    hash: str | None
    display_id: int
    flags: set[str]
    frame: Rect
    touchable_region: str

    @property
    def touchable(self) -> bool:
        return not ({"NOT_TOUCHABLE", "NOT_VISIBLE", "NO_INPUT_CHANNEL"} & self.flags)


@dataclass
class InputState:
    focused_display: int | None
    focused_app_task: int | None
    focused_window: str | None
    focused_window_hash: str | None
    windows: list[InputWindow]


_INPUT_WIN = re.compile(r"^\s+\d+: name=(.*?), id=\d+, displayId=(-?\d+), inputConfig=(.*?), alpha=[\d.]+, "
                        r"frame=(" + _RECT + r"),.*?touchableRegion=(<empty>|" + _RECT + r"|\S+)")


def parse_input(text: str, display_id: int = 0) -> InputState:
    if "FocusedWindows:" not in text or "FocusedApplications:" not in text:
        raise EvidenceError("input: focus section missing")
    m = re.search(r"FocusedDisplayId: (-?\d+)", text)
    fdisp = int(m.group(1)) if m else None
    app_task = None
    m = re.search(r"FocusedApplications:\s*\n((?:\s+displayId=.*\n)*)", text)
    if m:
        for line in m.group(1).splitlines():
            mm = re.search(r"displayId=(-?\d+), name='ActivityRecord\{\S+ u\d+ \S+ t(-?\d+)\}'", line)
            if mm and int(mm.group(1)) == display_id:
                app_task = int(mm.group(2))
    fwin = None
    m = re.search(r"FocusedWindows:\s*\n((?:\s+displayId=.*\n)*)", text)
    if m:
        for line in m.group(1).splitlines():
            mm = re.search(r"displayId=(-?\d+), name='([^']*)'", line)
            if mm and int(mm.group(1)) == display_id:
                fwin = mm.group(2)
    wins = []
    for line in text.splitlines():
        mm = _INPUT_WIN.match(line)
        if mm:
            name = mm.group(1)
            h = name.split(" ", 1)[0]
            wins.append(InputWindow(name, h if re.fullmatch(r"[0-9a-f]{5,8}", h) else None, int(mm.group(2)),
                                    {f.strip() for f in mm.group(3).split("|")}, Rect.parse(mm.group(4)),
                                    mm.group(5)))
    if not wins:
        raise EvidenceError("input: no input windows listed")
    fhash = None
    if fwin:
        h = fwin.split(" ", 1)[0]
        fhash = h if re.fullmatch(r"[0-9a-f]{5,8}", h) else None
    return InputState(fdisp, app_task, fwin, fhash, wins)


# ---------------------------------------------------------------------------------------------------------------------
# small command outputs

_TRUE = ("1", "y", "yes", "on", "true")
_FALSE = ("0", "n", "no", "off", "false")


def android_bool(value: str | None, default: bool = False) -> bool | None:
    """A system property as SystemProperties.getBoolean reads it (android::base::ParseBool): 1/y/yes/on/true and
    0/n/no/off/false; unset or empty gives the default. Anything else is unknown (None) here, never guessed."""
    if value is None or value == "":
        return default
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return None


def same_bool_prop(a: str | None, b: str | None) -> bool:
    """Equal as the framework reads them (true == 1, unset == 0); unparsable values must match exactly."""
    pa, pb = android_bool(a), android_bool(b)
    if pa is None or pb is None:
        return a == b
    return pa == pb

def parse_device_state(text: str) -> tuple[int, str]:
    m = re.search(r"mCommittedState=Optional\[DeviceState\{identifier=(\d+), name='([^']*)'", text)
    if not m:
        raise EvidenceError("device_state: mCommittedState missing")
    return int(m.group(1)), m.group(2)


def parse_getprop(text: str) -> dict[str, str]:
    props = dict(re.findall(r"(?m)^\[([^\]]+)\]: \[(.*)\]\s*$", text))
    if not props:
        raise EvidenceError("getprop: no properties")
    return props


def parse_resolved_home(text: str) -> str:
    comps = [l.strip() for l in text.splitlines() if re.fullmatch(r"[\w.]+/[\w.$]+", l.strip())]
    if len(comps) != 1:
        raise EvidenceError("HOME: cannot resolve one component from %r" % text.strip()[:200])
    return comps[0]


def parse_wm_size(text: str) -> tuple[tuple[int, int], tuple[int, int] | None]:
    m = re.search(r"Physical size: (\d+)x(\d+)", text)
    if not m:
        raise EvidenceError("wm size: no physical size")
    o = re.search(r"Override size: (\d+)x(\d+)", text)
    return (int(m.group(1)), int(m.group(2))), ((int(o.group(1)), int(o.group(2))) if o else None)


def parse_wm_density(text: str) -> tuple[int, int | None]:
    m = re.search(r"Physical density: (\d+)", text)
    if not m:
        raise EvidenceError("wm density: no physical density")
    o = re.search(r"Override density: (\d+)", text)
    return int(m.group(1)), (int(o.group(1)) if o else None)


def parse_display_offset(text: str) -> tuple[int, int] | None:
    m = re.search(r"mDisplayOffset=\((-?\d+), (-?\d+)\)", text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_package(text: str) -> dict[str, str]:
    out = {}
    for key in ("versionName", "versionCode", "codePath", "lastUpdateTime"):
        m = re.search(r"\b" + key + r"=(\S+)", text)
        if m:
            out[key] = m.group(1)
    if "codePath" not in out:
        raise EvidenceError("package: not installed or unexpected dumpsys package output")
    return out


def parse_credential_type(text: str) -> str:
    m = re.search(r"CredentialType: (\w+)", text)
    if not m:
        raise EvidenceError("lock_settings: CredentialType not found")
    return m.group(1).upper()


def parse_recents_component(text: str) -> str | None:
    m = re.search(r"mRecentsComponent=(?:ComponentInfo\{)?([\w.]+/[\w.$]+)", text)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------------------------------------------------
# dropbox

@dataclass
class DropboxEntry:
    time: str
    tag: str
    body: str


def parse_dropbox(text: str) -> list[DropboxEntry]:
    """DropBoxManagerService.dump: a header, then per entry "<yyyy-MM-dd HH:mm:ss> <tag> (...)"; with --print the
    entry text follows unindented, entries separated by a line of '='."""
    if not re.search(r"Drop box contents: \d+ entries", text):
        raise EvidenceError("dropbox: header missing")
    entries: list[DropboxEntry] = []
    for line in text.splitlines():
        m = re.match(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (\S+) \(", line)
        if m:
            entries.append(DropboxEntry(m.group(1), m.group(2), ""))
        elif entries and not re.fullmatch(r"=+", line.strip()):
            entries[-1].body += line.strip() + "\n"
    return entries


# ---------------------------------------------------------------------------------------------------------------------
# logcat

@dataclass
class LogLine:
    seq: int
    date: str
    time: str
    pid: int
    level: str
    tag: str
    msg: str
    raw: str

    @property
    def stamp(self) -> str:
        return self.date + " " + self.time


_THREADTIME = re.compile(r"^(\d\d-\d\d) (\d\d:\d\d:\d\d\.\d+)\s+(\d+)\s+(\d+)\s+([VDIWEFS])\s(.*?)\s*: (.*)$")
_TIMEFMT = re.compile(r"^(\d\d-\d\d) (\d\d:\d\d:\d\d\.\d+) ([VDIWEFS])/(.*?)\(\s*(\d+)\): (.*)$")


def parse_log_line(line: str, seq: int = 0) -> LogLine | None:
    line = line.rstrip("\r\n")
    m = _THREADTIME.match(line)
    if m:
        return LogLine(seq, m.group(1), m.group(2), int(m.group(3)), m.group(5), m.group(6).strip(), m.group(7), line)
    m = _TIMEFMT.match(line)
    if m:
        return LogLine(seq, m.group(1), m.group(2), int(m.group(5)), m.group(3), m.group(4).strip(), m.group(6), line)
    return None


def parse_logcat(text: str) -> list[LogLine]:
    out = []
    for line in text.splitlines():
        ll = parse_log_line(line, len(out))
        if ll:
            out.append(ll)
    return out


_KEY = re.compile(r"(?:^|\s)([A-Za-z][\w.]*)=")


def parse_kv(text: str) -> dict[str, str]:
    """key=value pairs where a value runs to the next " key=" (values such as config={...} contain spaces)."""
    keys = list(_KEY.finditer(text))
    out = {}
    for i, m in enumerate(keys):
        end = keys[i + 1].start() if i + 1 < len(keys) else len(text)
        out.setdefault(m.group(1), text[m.end():end].strip())
    return out


@dataclass
class ProbeEvent:
    seq: int
    stamp: str
    event: str
    act: str
    task: int
    kv: dict[str, str]

    @property
    def act_class(self) -> str:
        return self.act.split("@", 1)[0]

    def rect(self, key: str) -> Rect | None:
        v = self.kv.get(key)
        if v is None or v == "?":
            return None
        return Rect.parse(v.split()[0])


def probe_event(line: LogLine) -> ProbeEvent | None:
    if line.tag != "DuoProbe":
        return None
    m = re.match(r"event=(\S+) act=(\S+) task=(-?\d+)(?: (.*))?$", line.msg)
    if not m:
        return None
    return ProbeEvent(line.seq, line.stamp, m.group(1), m.group(2), int(m.group(3)), parse_kv(m.group(4) or ""))


def parse_targets(event: ProbeEvent) -> dict[str, Rect]:
    out = {}
    for k, v in event.kv.items():
        try:
            out[k] = Rect.parse(v)
        except EvidenceError:
            raise EvidenceError("targets: bad rectangle for %s: %r" % (k, v))
    return out


def marker_id(line: LogLine) -> str | None:
    if line.tag != "DuoGate":
        return None
    m = re.match(r"mark id=(\S+)", line.msg)
    return m.group(1) if m else None


def posture_composition(line: LogLine) -> int | None:
    if line.tag != "POSTURE PROCESSOR":
        return None
    m = re.match(r"Setting display composition (\d+)", line.msg)
    return int(m.group(1)) if m else None


_MINIMIZE = re.compile(r"(Home|Home from Recents|Recents #\d+): minimizing (Panel\d+) \((\d+) tasks\)")


def policy_minimized_panel(line: LogLine) -> tuple[str, int] | None:
    """A panel minimized by the policy: Home on the focused panel (fw 0008/0014), and since M2.2 (fw 0018) also
    "Home from Recents" (Quickstep gesture Home, fallback Recents) and "Recents #<id>" (end of a panel Recents
    session that went home)."""
    m = minimize_event(line)
    return (m[1], m[2]) if m else None


def minimize_event(line: LogLine) -> tuple[str, str, int] | None:
    """(reason, panel, task count) of a policy minimize log line."""
    if line.tag != "DuoDisplayAreaPolicy":
        return None
    m = _MINIMIZE.match(line.msg)
    return (m.group(1), m.group(2), int(m.group(3))) if m else None


def policy_restored_task(line: LogLine) -> int | None:
    if line.tag != "DuoDisplayAreaPolicy":
        return None
    m = re.match(r"Restored minimized Task\{\S+ #(\d+)", line.msg)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------------------------------------------------
# uiautomator dump

@dataclass
class UiNode:
    text: str
    cls: str
    package: str
    bounds: Rect
    focused: bool


def parse_uiautomator(text: str) -> list[UiNode]:
    i = text.find("<?xml")
    if i < 0:
        i = text.find("<hierarchy")
    if i < 0:
        raise EvidenceError("uiautomator: no XML hierarchy")
    try:
        root = ET.fromstring(text[i:].strip())
    except ET.ParseError as e:
        raise EvidenceError("uiautomator: %s" % e)
    out = []
    for n in root.iter("node"):
        b = n.get("bounds")
        if not b:
            continue
        out.append(UiNode(n.get("text", ""), n.get("class", ""), n.get("package", ""), Rect.parse(b),
                          n.get("focused") == "true"))
    if not out:
        raise EvidenceError("uiautomator: empty hierarchy")
    return out
