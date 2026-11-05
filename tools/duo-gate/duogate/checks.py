"""Geometry constants and pure evaluation functions (no device access), shared by the gates and the offline tests.

Expected geometry comes from literal Duo 1 constants, never from the platform code under test. Each function returns a
Verdict; missing or ambiguous evidence gives INCOMPLETE, contradicting evidence gives FAIL.
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import EvidenceError, Rect, Verdict
from .parsers import (DropboxEntry, LogLine, ProbeEvent, policy_minimized_panel, policy_restored_task)

POLICY_HIDDEN = 8          # FLAG_FORCE_HIDDEN_FOR_DUO_PANEL (frameworks_base 0014)
SIDES = ("A", "B")         # A = left panel 0, B = right panel 1 (unrotated, unfolded)
TEST_CLASSES = ("ProbeTestActivity", "ProbeOtherTestActivity", "ProbeLockedTestActivity")
PROBE_PACKAGE = "org.duode.probe"


@dataclass(frozen=True)
class Geometry:
    display: Rect
    panels: dict
    hinge: Rect

    def panel(self, side: str) -> Rect:
        return self.panels[side]

    @staticmethod
    def area(side: str) -> str:
        return "Panel%dTasks" % SIDES.index(side)

    @staticmethod
    def group(side: str) -> str:
        return "Panel%d" % SIDES.index(side)

    def side_of_point(self, x: int, y: int) -> str | None:
        for s in SIDES:
            if self.panels[s].contains_point(x, y):
                return s
        return None


# Surface Duo 1, rotation 0, both panels lit: 2784x1800 at 400 dpi, 84 px hinge band.
DUO1 = Geometry(Rect(0, 0, 2784, 1800), {"A": Rect(0, 0, 1350, 1800), "B": Rect(1434, 0, 2784, 1800)},
                Rect(1350, 0, 1434, 1800))


def other(side: str) -> str:
    return "B" if side == "A" else "A"


def _ev(events, task=None, name=None, cls=None):
    return [e for e in events if (task is None or e.task == task) and (name is None or e.event in (
        (name,) if isinstance(name, str) else name)) and (cls is None or e.act_class == cls)]


def _snap(fn, v: Verdict, what: str):
    """Runs a snapshot parser; parser errors become INCOMPLETE on v and return None."""
    try:
        return fn()
    except EvidenceError as e:
        v.incomplete("%s: %s" % (what, e))
        return None


# ---------------------------------------------------------------------------------------------------------------------
# geometry / placement

def check_geometry(snap, geo: Geometry = DUO1) -> Verdict:
    """The panel task areas must be exactly the Duo 1 constants (rotation 0, dual posture)."""
    v = Verdict()
    c = _snap(lambda: snap.containers, v, "containers")
    if c is None:
        return v
    for s in SIDES:
        n = _snap(lambda: c.find(Geometry.area(s)), v, "containers")
        if n is None:
            v.incomplete("%s missing: the two panel policy is not active" % Geometry.area(s))
        elif n.bounds != geo.panel(s):
            v.fail("%s bounds %s, expected %s" % (n.name, n.bounds, geo.panel(s)))
    d = c.displays()
    if d and d[0].bounds != geo.display:
        v.incomplete("display bounds %s, expected %s (rotated or resized?)" % (d[0].bounds, geo.display))
    return v


def check_placement(snap, task_id: int, side: str, geo: Geometry = DUO1, mode: str = "fullscreen") -> Verdict:
    v = Verdict()
    c = _snap(lambda: snap.containers, v, "containers")
    if c is None:
        return v
    node = _snap(lambda: c.task(task_id), v, "containers")
    if node is None:
        return v.incomplete("task %d is not in the containers dump" % task_id) if v.ok else v
    tda = c.task_display_area(node)
    if tda is None:
        return v.incomplete("task %d has no task display area ancestor" % task_id)
    if tda.name != Geometry.area(side):
        v.fail("task %d is in %s, expected %s" % (task_id, tda.name, Geometry.area(side)))
    if node.mode != mode:
        v.fail("task %d mode=%s, expected %s" % (task_id, node.mode, mode))
    if node.bounds != geo.panel(side):
        v.fail("task %d bounds %s, expected %s" % (task_id, node.bounds, geo.panel(side)))
    return v


def check_default_area(snap, task_id: int, geo: Geometry = DUO1, mode: str = "fullscreen") -> Verdict:
    v = Verdict()
    c = _snap(lambda: snap.containers, v, "containers")
    if c is None:
        return v
    node = _snap(lambda: c.task(task_id), v, "containers")
    if node is None:
        return v.incomplete("task %d is not in the containers dump" % task_id) if v.ok else v
    tda = c.task_display_area(node)
    if tda is None or tda.name != "DefaultTaskDisplayArea":
        v.fail("task %d is in %s, expected DefaultTaskDisplayArea" % (task_id, tda.name if tda else None))
    if node.mode != mode:
        v.fail("task %d mode=%s, expected %s" % (task_id, node.mode, mode))
    if node.bounds != geo.display:
        v.fail("task %d bounds %s, expected the display %s" % (task_id, node.bounds, geo.display))
    return v


def check_not_policy_hidden(snap, task_id: int) -> Verdict:
    v = Verdict()
    acts = _snap(lambda: snap.activities, v, "activities")
    if acts is None:
        return v
    t = acts.task(task_id)
    if t is None:
        return v.incomplete("task %d is not in the activities dump" % task_id)
    if t.force_hidden & POLICY_HIDDEN:
        v.fail("task %d still hidden by the panel policy (mForceHiddenFlags=%s)" % (task_id, t.force_hidden_raw))
    elif t.force_hidden:
        v.note("task %d has other hidden reasons (mForceHiddenFlags=%s)" % (task_id, t.force_hidden_raw))
    if t.visible is False:
        v.fail("task %d is not visible" % task_id)
    return v


# ---------------------------------------------------------------------------------------------------------------------
# launch / reuse

def eval_launch(events: list[ProbeEvent], snap, side: str, *, label: str, act_class: str,
                display_id: str = "unset", expect_task: int | None = None, exclude_tasks=(),
                geo: Geometry = DUO1, area: str = "panel") -> tuple[Verdict, int | None]:
    """A tap on the probe home launched/reused `act_class` on `side` (area="panel") or in the default area."""
    v = Verdict()
    launches = _ev(events, name="launch", cls="ProbeHomeActivity")
    if not launches:
        return v.incomplete("no launch event from the probe home after the marker (tap missed the button?)"), None
    if len(launches) > 1:
        v.incomplete("%d launch events after one tap (ambiguous)" % len(launches))
    L = launches[0]
    if L.kv.get("side") != side or L.kv.get("label") != label.replace(" ", "_"):
        v.incomplete("tap reached side=%s label=%s, intended side=%s label=%s (stale tap coordinates)"
                     % (L.kv.get("side"), L.kv.get("label"), side, label.replace(" ", "_")))
    if L.kv.get("launchDisplayId") != display_id:
        v.incomplete("launchDisplayId=%s, intended %s" % (L.kv.get("launchDisplayId"), display_id))
    v.note("requested windowingMode=%s launchDisplayId=%s" % (L.kv.get("windowingMode"), L.kv.get("launchDisplayId")))
    resumes = [e for e in _ev(events, name="onResume", cls=act_class) if e.seq > L.seq and e.task not in exclude_tasks]
    tasks = sorted({e.task for e in resumes})
    if not tasks:
        return v.incomplete("no onResume of %s after the launch" % act_class), None
    if len(tasks) > 1:
        return v.incomplete("ambiguous identity: %s resumed in tasks %s" % (act_class, tasks)), None
    tid = tasks[0]
    if expect_task is not None and tid != expect_task:
        v.fail("reuse expected task %d, got task %d (a new task was created)" % (expect_task, tid))
    r = [e for e in resumes if e.task == tid][-1]
    bounds = r.rect("bounds")
    want = geo.panel(side) if area == "panel" else geo.display
    if bounds is None:
        v.incomplete("onResume of task %d has no bounds" % tid)
    elif bounds != want:
        v.fail("task %d window bounds %s, expected %s" % (tid, bounds, want))
    if r.kv.get("multiWindow") not in (None, "false"):
        v.note("multiWindow=%s" % r.kv.get("multiWindow"))
    if area == "panel":
        v.merge(check_placement(snap, tid, side, geo))
    else:
        v.merge(check_default_area(snap, tid, geo))
    v.merge(check_not_policy_hidden(snap, tid))
    return v, tid


# ---------------------------------------------------------------------------------------------------------------------
# real input receipt

def eval_touch(events: list[ProbeEvent], x: int, y: int, target_task: int, other_tasks=(), tol: int = 3) -> Verdict:
    """A generated tap at (x, y) must be received by the target task and by no other probe task."""
    v = Verdict()
    downs = [e for e in _ev(events, name="touch") if e.kv.get("action") == "down"]
    for e in downs:
        if e.task in other_tasks:
            v.fail("touch delivered to task %d (%s), not the target %d" % (e.task, e.act_class, target_task))
    hits = []
    for e in downs:
        try:
            rx, ry = int(e.kv.get("rawX", "x")), int(e.kv.get("rawY", "x"))
        except ValueError:
            v.incomplete("unparsable touch event %s" % e.kv)
            continue
        if abs(rx - x) <= tol and abs(ry - y) <= tol:
            hits.append(e)
    mine = [e for e in hits if e.task == target_task]
    strangers = [e for e in hits if e.task != target_task and e.task not in other_tasks]
    for e in strangers:
        v.fail("touch at (%d,%d) reached task %d (%s) instead of task %d" % (x, y, e.task, e.act_class, target_task))
    if not mine and not strangers and v.ok:
        v.incomplete("no probe logged the touch at (%d,%d) (probe without touch logging, or input lost)" % (x, y))
    return v


def eval_no_touch(events: list[ProbeEvent], forbidden_tasks) -> Verdict:
    v = Verdict()
    for e in _ev(events, name=("touch", "text", "editorFocus")):
        if e.task in forbidden_tasks:
            v.fail("task %d received %s %s" % (e.task, e.event, e.kv.get("action", "")))
    return v


def eval_text(events: list[ProbeEvent], token: str, target_task: int, other_tasks=()) -> Verdict:
    """A typed sentinel token must reach the target task's editor and no other probe."""
    v = Verdict()
    got = [e for e in _ev(events, name="text") if token in e.kv.get("sentinels", "").split(",")]
    for e in got:
        if e.task != target_task:
            v.fail("sentinel %s typed into task %d (%s), not the target %d" % (token, e.task, e.act_class, target_task))
    if not any(e.task == target_task for e in got) and v.ok:
        v.incomplete("sentinel %s was not received by any probe" % token)
    return v


def eval_focus(snap, task_id: int) -> Verdict:
    """WM focus, focused app and input dispatcher focus all name the target task."""
    v = Verdict()
    wd = _snap(lambda: snap.wdisplays, v, "window displays")
    ww = _snap(lambda: snap.windows, v, "window windows")
    inp = _snap(lambda: snap.input, v, "input")
    if wd is None or ww is None or inp is None:
        return v
    if wd.current_focus is None:
        return v.fail("no focused window")
    w = ww.get(wd.current_focus.hash)
    if w is None:
        return v.incomplete("focused window %s not in the window list" % wd.current_focus.hash)
    if w.task_id != task_id:
        v.fail("focused window %s belongs to task %s, expected %d" % (w.title, w.task_id, task_id))
    if wd.focused_app_task != task_id:
        v.fail("focused app is task %s, expected %d" % (wd.focused_app_task, task_id))
    if inp.focused_window_hash != wd.current_focus.hash:
        v.fail("input focus %r differs from WM focus %s" % (inp.focused_window, wd.current_focus.hash))
    if inp.focused_app_task != task_id:
        v.fail("input focused application is task %s, expected %d" % (inp.focused_app_task, task_id))
    return v


# ---------------------------------------------------------------------------------------------------------------------
# Home / Back

def eval_home(lines: list[LogLine], events: list[ProbeEvent], snap, home_side: str, target_task: int,
              other_task: int, geo: Geometry = DUO1) -> Verdict:
    """Home on home_side minimized that panel only; the other panel's task stays placed, visible and not stopped."""
    v = Verdict()
    minimized = [m for m in (policy_minimized_panel(l) for l in lines) if m]
    if not minimized:
        v.incomplete("no 'Home: minimizing' log after the marker (Home not delivered or not recognized)")
    for panel, n in minimized:
        if panel != Geometry.group(home_side):
            v.fail("Home minimized %s, expected %s" % (panel, Geometry.group(home_side)))
    acts = _snap(lambda: snap.activities, v, "activities")
    if acts is not None:
        t, o = acts.task(target_task), acts.task(other_task)
        if t is None or o is None:
            v.incomplete("task %s missing from the activities dump" % (target_task if t is None else other_task))
        else:
            if not t.force_hidden & POLICY_HIDDEN and minimized:
                v.fail("task %d on the Home panel is not hidden by the policy" % target_task)
            if o.force_hidden & POLICY_HIDDEN:
                v.fail("task %d on the other panel was hidden by Home" % other_task)
            if o.visible is False:
                v.fail("task %d on the other panel is not visible" % other_task)
    for e in _ev(events, task=other_task, name=("onStop", "onDestroy")):
        v.fail("other panel's task %d got %s" % (other_task, e.event))
    pauses = _ev(events, task=other_task, name="onPause")
    if pauses:
        v.note("other panel's task %d paused (not stopped): acceptable, recorded" % other_task)
    if not _ev(events, task=target_task, name="onStop"):
        v.note("no onStop from the minimized task %d within the settle time" % target_task)
    v.merge(check_placement(snap, other_task, other(home_side), geo))
    return v


def eval_back(events: list[ProbeEvent], snap, back_side: str, target_task: int, other_task: int,
              geo: Geometry = DUO1) -> Verdict:
    """A Back gesture from back_side's edge acted on that panel's task only."""
    v = Verdict()
    for e in _ev(events, task=other_task, name=("onStop", "onDestroy")):
        v.fail("Back acted on the other panel: task %d got %s" % (other_task, e.event))
    for e in _ev(events, name="backInvokedOnHome"):
        v.fail("Back went to the probe home instead of task %d" % target_task)
    stopped = _ev(events, task=target_task, name=("onStop", "onDestroy"))
    paused = _ev(events, task=target_task, name="onPause")
    if not stopped:
        if paused:
            v.incomplete("task %d paused but did not stop (gesture cancelled?)" % target_task)
        elif v.ok:
            v.incomplete("no lifecycle change on either panel: the Back gesture was not recognized")
    v.merge(check_placement(snap, other_task, other(back_side), geo))
    v.merge(check_not_policy_hidden(snap, other_task))
    return v


def eval_restored(lines: list[LogLine], task_id: int) -> Verdict:
    v = Verdict()
    restored = [t for t in (policy_restored_task(l) for l in lines) if t is not None]
    if task_id not in restored:
        v.incomplete("no 'Restored minimized' log for task %d" % task_id)
    return v


# ---------------------------------------------------------------------------------------------------------------------
# IME

def _insets_ime(e: ProbeEvent) -> tuple[bool, Rect | None]:
    vis = e.kv.get("imeVisible") == "true"
    raw = e.kv.get("ime", "")
    try:
        l, t, r, b = (int(x) for x in raw.strip("[]").split(","))
        return vis, Rect(l, t, r, b)
    except ValueError:
        return vis, None


def eval_ime(snap, side: str, editor: int, target_task: int, other_task: int, *, target_focus: ProbeEvent | None,
             target_insets: ProbeEvent | None, other_insets: ProbeEvent | None, geo: Geometry = DUO1) -> Verdict:
    """After one tap on an editor (the tap itself is proven by eval_touch): the editor holds focus, the keyboard is
    placed on the target panel, the IME targets are the target task's window, and only the target task has non-zero
    IME insets (imeVisible alone is not evidence: non-target windows may report it with zero insets).

    target_focus / target_insets / other_insets are the latest such events of each task up to the end of the step
    (state, not proof of action); the dumps are taken after the marker."""
    v = Verdict()
    if target_focus is None or target_focus.kv.get("editor") != str(editor) or target_focus.kv.get("focus") != "true":
        v.fail("editor %d of task %d does not hold focus after one tap (last editorFocus: %s)"
               % (editor, target_task, target_focus.kv if target_focus else None))
    if target_insets is None:
        v.incomplete("task %d never logged insets" % target_task)
    else:
        vis, rect = _insets_ime(target_insets)
        if rect is None:
            v.incomplete("unparsable insets %s" % target_insets.kv)
        elif not vis or rect.bottom <= 0:
            v.fail("task %d: imeVisible=%s ime=%s, keyboard not shown" % (target_task, vis,
                                                                           target_insets.kv.get("ime")))
    if other_insets is None:
        v.incomplete("no insets ever logged by the other task %d" % other_task)
    else:
        vis, rect = _insets_ime(other_insets)
        if rect is None:
            v.incomplete("unparsable insets %s" % other_insets.kv)
        elif rect.left or rect.top or rect.right or rect.bottom:
            v.fail("other task %d got IME insets %s" % (other_task, other_insets.kv.get("ime")))
    c = _snap(lambda: snap.containers, v, "containers")
    if c is not None:
        ime = _snap(lambda: c.ime_container(), v, "containers")
        if ime is None:
            v.incomplete("ImeContainer not in the containers dump")
        else:
            if ime.bounds != geo.panel(side):
                v.fail("ImeContainer bounds %s, expected panel %s %s" % (ime.bounds, side, geo.panel(side)))
            if ime.requested_bounds is not None and ime.requested_bounds != geo.panel(side):
                v.fail("ImeContainer requested-bounds %s, expected %s" % (ime.requested_bounds, geo.panel(side)))
    wd = _snap(lambda: snap.wdisplays, v, "window displays")
    ww = _snap(lambda: snap.windows, v, "window windows")
    if wd is not None and ww is not None:
        for label, ref in (("input", wd.ime_input_target), ("layering", wd.ime_layering_target),
                           ("control", wd.ime_control_target)):
            if ref is None:
                if label != "control":
                    v.fail("no IME %s target" % label)
                continue
            w = ww.get(ref.hash)
            if w is None:
                v.incomplete("IME %s target %s not in the window list" % (label, ref.hash))
            elif w.task_id != target_task:
                v.fail("IME %s target %s is task %s, expected %d" % (label, ref.title, w.task_id, target_task))
    return v


def eval_rotation(snap, requested: int, settled: bool) -> Verdict:
    """The action-scoped rotation evidence arrived (settled) and the display really is at the requested rotation
    (user_rotation value 0..3), read from the display's own configuration. Otherwise nothing can be judged."""
    v = Verdict()
    if not settled:
        v.incomplete("no rotation=%d event from the probe tasks within the settle time" % requested)
    wd = _snap(lambda: snap.wdisplays, v, "window displays")
    if wd is not None:
        if wd.rotation is None:
            v.incomplete("display rotation not found in the window displays dump")
        elif wd.rotation != requested * 90:
            v.incomplete("display at rotation %d, requested %d: the rotation did not take effect"
                         % (wd.rotation, requested * 90))
    return v


def eval_ime_rotated(events: list[ProbeEvent], snap, target_task: int, other_task: int) -> Verdict:
    """After a rotation with the keyboard up: the keyboard is confined to the panel area holding the target task (the
    rotated panel bounds are taken from the same dump), and both tasks kept their panel areas."""
    v = Verdict()
    c = _snap(lambda: snap.containers, v, "containers")
    if c is None:
        return v
    t, o = c.task(target_task), c.task(other_task)
    if t is None or o is None:
        return v.fail("task %s lost after rotation" % (target_task if t is None else other_task))
    ta, oa = c.task_display_area(t), c.task_display_area(o)
    if ta is None or oa is None or ta.name == oa.name or not ta.name.startswith("Panel"):
        return v.fail("tasks not on two panels after rotation (%s, %s)" % (ta and ta.name, oa and oa.name))
    if ta.bounds.intersects(oa.bounds):
        v.fail("panel areas overlap after rotation: %s %s" % (ta.bounds, oa.bounds))
    ins = _ev(events, task=target_task, name="insets")
    if not ins or not _insets_ime(ins[-1])[0]:
        return v.incomplete("keyboard not up for task %d after rotation (probe relaunched without configChanges)"
                            % target_task)
    ime = c.ime_container()
    if ime is None:
        return v.incomplete("ImeContainer missing")
    if ime.bounds != ta.bounds:
        v.fail("ImeContainer %s not equal to the target's panel %s after rotation" % (ime.bounds, ta.bounds))
    return v


# ---------------------------------------------------------------------------------------------------------------------
# keyguard

def eval_locked(events: list[ProbeEvent], snap, ordinary_tasks, showwhenlocked_task: int | None = None) -> Verdict:
    """While locked: keyguard showing; every ordinary task window explicitly reported off screen and invisible (an
    unparsed visibility is never taken as hidden); none touchable in the input dispatcher; neither the WM nor the
    input dispatcher (checked independently) focuses an ordinary task; and no input or resume reached an ordinary
    task (attempted interaction included)."""
    v = Verdict()
    acts = _snap(lambda: snap.activities, v, "activities")
    if acts is not None:
        if acts.keyguard_showing is None:
            v.incomplete("keyguard state not found in the activities dump")
        elif not acts.keyguard_showing:
            v.incomplete("keyguard not showing after lock (screen lock not secure or lock delay set?)")
    ww = _snap(lambda: snap.windows, v, "window windows")
    inp = _snap(lambda: snap.input, v, "input")
    wd = _snap(lambda: snap.wdisplays, v, "window displays")
    if ww is not None:
        for tid in ordinary_tasks:
            if not any(w.task_id == tid for w in ww.values()):
                v.incomplete("no window attributable to ordinary task %d in the window dump" % tid)
        for w in ww.values():
            if w.task_id not in ordinary_tasks:
                continue
            if w.on_screen or w.visible:
                v.fail("ordinary task %d window %s visible while locked (isOnScreen=%s isVisible=%s)"
                       % (w.task_id, w.title, w.on_screen, w.visible))
            elif w.on_screen is None or w.visible is None:
                v.incomplete("visibility of ordinary task %d window %s not reported (isOnScreen=%s isVisible=%s)"
                             % (w.task_id, w.title, w.on_screen, w.visible))
            if inp is not None:
                for iw in inp.windows:
                    if iw.hash == w.hash and iw.touchable:
                        v.fail("ordinary task %d window %s touchable while locked (%s)"
                               % (w.task_id, w.title, "|".join(sorted(iw.flags))))
        if wd is not None:
            if wd.current_focus is None:
                v.incomplete("WM reports no focused window while locked")
            else:
                fw = ww.get(wd.current_focus.hash)
                if fw is None:
                    v.incomplete("WM focus %s not in the window dump" % wd.current_focus.hash)
                elif fw.task_id in ordinary_tasks:
                    v.fail("ordinary task %d has WM focus while locked" % fw.task_id)
        if inp is not None:
            if inp.focused_app_task in ordinary_tasks:
                # FocusedApplications is ANR/wait context only; keys go to the focused window (checked below)
                v.note("input dispatcher focused application is ordinary task %d (diagnostic only)"
                       % inp.focused_app_task)
            if inp.focused_window_hash is None:
                v.incomplete("input dispatcher focused window missing or unresolvable (%r)" % inp.focused_window)
            else:
                iw = ww.get(inp.focused_window_hash)
                if iw is None:
                    v.incomplete("input focus %r not in the window dump" % inp.focused_window)
                elif iw.task_id in ordinary_tasks:
                    v.fail("ordinary task %d has input focus while locked" % iw.task_id)
                if wd is not None and wd.current_focus is not None and wd.current_focus.hash != inp.focused_window_hash:
                    v.note("WM focus %s and input focus %r differ" % (wd.current_focus.hash, inp.focused_window))
    for e in _ev(events, name=("onResume", "touch", "text", "editorFocus")):
        if e.task in ordinary_tasks:
            v.fail("ordinary task %d got %s while locked" % (e.task, e.event))
    if showwhenlocked_task is not None and acts is not None:
        v.note("showWhenLocked task %d: keyguard occluded=%s" % (showwhenlocked_task, acts.keyguard_occluded))
    return v


def eval_occlusion(snap, showwhenlocked_task: int) -> Verdict:
    """Functional (F8): a showWhenLocked activity on a panel occludes the keyguard and is on screen."""
    v = Verdict()
    acts = _snap(lambda: snap.activities, v, "activities")
    ww = _snap(lambda: snap.windows, v, "window windows")
    if acts is None or ww is None:
        return v
    if acts.keyguard_occluded is not True:
        v.fail("keyguard not occluded by the showWhenLocked task %d (Occluded=%s)"
               % (showwhenlocked_task, acts.keyguard_occluded))
    if not any(w.task_id == showwhenlocked_task and w.on_screen for w in ww.values()):
        v.fail("showWhenLocked task %d not on screen while locked" % showwhenlocked_task)
    return v


def eval_unlocked(snap, placements: dict, geo: Geometry = DUO1) -> Verdict:
    """After unlock: keyguard gone; every task kept its id and panel (placements: task id -> side)."""
    v = Verdict()
    acts = _snap(lambda: snap.activities, v, "activities")
    if acts is not None:
        if acts.keyguard_showing is None:
            v.incomplete("keyguard state not found")
        elif acts.keyguard_showing:
            v.incomplete("still locked after the unlock attempt")
    for tid, side in placements.items():
        v.merge(check_placement(snap, tid, side, geo))
    return v


# ---------------------------------------------------------------------------------------------------------------------
# posture / manual-gate invariants

def eval_invariants(snap, tasks: dict, allowed_hidden=(), expect_sides: bool = False,
                    geo: Geometry = DUO1) -> Verdict:
    """No task lost; no stranded policy-hidden flag outside allowed_hidden; with expect_sides, every task back on its
    own panel (tasks: task id -> side)."""
    v = Verdict()
    c = _snap(lambda: snap.containers, v, "containers")
    acts = _snap(lambda: snap.activities, v, "activities")
    if c is None or acts is None:
        return v
    for tid, side in tasks.items():
        node = c.task(tid)
        if node is None:
            v.fail("task %d lost" % tid)
            continue
        tda = c.task_display_area(node)
        v.note("task %d: %s mode=%s bounds=%s" % (tid, tda.name if tda else None, node.mode, node.bounds))
        if expect_sides:
            v.merge(check_placement(snap, tid, side, geo))
    for t in acts.tasks.values():
        if t.force_hidden & POLICY_HIDDEN and t.task_id not in allowed_hidden:
            v.fail("task %d has a stranded policy-hidden flag (mForceHiddenFlags=%s)" % (t.task_id, t.force_hidden_raw))
    return v


def eval_panel_off(snap, props: dict) -> Verdict:
    v = Verdict()
    if props.get("sys.duode.panels.active") != "0":
        v.fail("sys.duode.panels.active=%r with both panel properties off" % props.get("sys.duode.panels.active"))
    for k in ("persist.sys.phh.duo.panels", "persist.debug.phh.duo.panels"):
        if props.get(k, "") not in ("", "0", "false"):
            v.incomplete("%s=%s, expected off" % (k, props.get(k)))
    c = _snap(lambda: snap.containers, v, "containers")
    if c is not None and c.panel_names():
        v.fail("panel areas present with panel mode off: %s" % c.panel_names())
    return v


# ---------------------------------------------------------------------------------------------------------------------
# G9

SYSTEM_SERVER_TAGS = ("system_server_crash", "system_server_native_crash", "system_server_anr",
                      "system_server_watchdog", "system_server_wtf_fatal")
SYSTEM_APP_TAGS = ("system_app_crash", "system_app_native_crash", "system_app_anr", "SYSTEM_TOMBSTONE")
ATTRIBUTABLE_PROCESSES = ("system_server", "com.android.systemui", "com.android.launcher3", PROBE_PACKAGE)


def eval_crashes(entries: list[DropboxEntry], since: str, lines: list[LogLine]) -> Verdict:
    """No system_server or SystemUI crash/ANR since the run started (dropbox entries newer than `since`, crash/ANR
    lines in the captured log). Other app crashes are recorded."""
    v = Verdict()
    for e in entries:
        if e.time < since:
            continue
        body = e.body
        if e.tag in SYSTEM_SERVER_TAGS:
            v.fail("%s at %s" % (e.tag, e.time))
        elif e.tag in SYSTEM_APP_TAGS:
            proc = next((p for p in ATTRIBUTABLE_PROCESSES if p in body), None)
            if proc in ("system_server", "com.android.systemui"):
                v.fail("%s in %s at %s" % (e.tag, proc, e.time))
            else:
                v.note("%s at %s (%s)" % (e.tag, e.time, (body.splitlines() or ["?"])[0][:120]))
    for l in lines:
        if l.tag == "AndroidRuntime" and "FATAL EXCEPTION IN SYSTEM PROCESS" in l.msg:
            v.fail("system_server fatal exception at %s" % l.stamp)
        if l.tag == "AndroidRuntime" and l.msg.startswith("Process: com.android.systemui"):
            v.fail("SystemUI crash at %s" % l.stamp)
        if l.tag == "ActivityManager" and l.msg.startswith("ANR in "):
            proc = l.msg[7:].split()[0]
            if proc in ("system", "system_server", "com.android.systemui"):
                v.fail("ANR in %s at %s" % (proc, l.stamp))
            else:
                v.note("ANR in %s at %s" % (proc, l.stamp))
    return v
