"""Gate procedures. Scripted actions are bracketed by markers and evaluated with checks.py; manual steps are guided
with prompts, evidence is collected around them, and they never PASS (see model.Check)."""
from __future__ import annotations

import time

from . import checks as C
from .adb import AdbError
from .checks import DUO1, SIDES, Geometry, other
from .device import HOME_BUTTONS, KEY_ENTER, KEY_HOME, KEY_POWER, KEY_WAKEUP, Device
from .model import INCOMPLETE, PASS, Check, EvidenceError, Verdict
from .parsers import android_bool, parse_dropbox
from . import preconditions
from .preconditions import PROBE_HOME


class SetupError(Exception):
    """A gate could not reach its starting state; its checks stay INCOMPLETE."""


class Ctx:
    def __init__(self, dev: Device, op, reps: int, pre: dict, options: dict | None = None):
        self.dev = dev
        self.op = op
        self.reps = reps
        self.pre = pre
        self.options = options or {}
        self.checks: list[Check] = []

    def check(self, cid: str, gate: str, title: str, required: int | None = None, manual: bool = False) -> Check:
        c = Check(cid, gate, title, max(1, self.reps if required is None else required), manual)
        self.checks.append(c)
        return c


def _record(check: Check, fn) -> Verdict:
    """Runs one attempt; evidence problems become an INCOMPLETE attempt. AdbError propagates (device lost)."""
    try:
        v, evidence, marker = fn()
    except (EvidenceError, SetupError) as e:
        v, evidence, marker = Verdict().incomplete(str(e)), [], None
    check.add(v, marker, evidence)
    return v


def _resumed(cls: str, task: int | None = None):
    return lambda evs: any(e.event == "onResume" and e.act_class == cls and (task is None or e.task == task)
                           for e in evs)


def _launched(dev: Device, cls: str):
    """onResume of cls, and its layout known (targets event, now or earlier: a reused task does not lay out again)."""
    def pred(evs):
        tasks = {e.task for e in evs if e.event == "onResume" and e.act_class == cls}
        return any(any(e.event == "targets" and e.task == t for e in evs)
                   or dev.log.latest(lambda e, t=t: e.event == "targets" and e.task == t) is not None
                   for t in tasks)
    return pred


def _touched(task: int):
    return lambda evs: any(e.event == "touch" and e.kv.get("action") == "up" and e.task == task for e in evs)


def _stopped(*tasks):
    return lambda evs: any(e.event in ("onStop", "onDestroy") and e.task in tasks for e in evs)


# ---------------------------------------------------------------------------------------------------------------------
# shared steps

def launch_from_home(ctx: Ctx, side: str, key: str, name: str, *, display_id: str = "unset",
                     expect_task: int | None = None, exclude=(), area: str = "panel"):
    dev = ctx.dev
    label, cls = HOME_BUTTONS[key]
    x, y = dev.point(dev.home_targets(), "%s.%s" % (side, key), side)
    st = dev.step(name, lambda: dev.tap(x, y), until=_launched(dev, cls))
    v, tid = C.eval_launch(st.events, st.snap, side, label=label, act_class=cls, display_id=display_id,
                           expect_task=expect_task, exclude_tasks=exclude, area=area, geo=dev.geo)
    return v, tid, st


def setup_pair(ctx: Ctx, keys=None, name: str = "setup") -> dict:
    """One reusable probe task per panel (default: Probe test on A, Probe test 2 on B), started from the probe home
    after removing earlier probe tasks. Returns side -> task id."""
    keys = keys or {"A": "probeTest", "B": "probeTest2"}
    dev = ctx.dev
    pair = {}
    try:
        dev.remove_probe_tasks()
        for side in SIDES:
            v, tid, st = launch_from_home(ctx, side, keys[side], "%s.%s" % (name, side),
                                          exclude=tuple(pair.values()))
            if tid is None or not v.ok:
                raise SetupError("setup launch on %s failed: %s" % (side, "; ".join(v.reasons)))
            pair[side] = tid
    except EvidenceError as e:
        raise SetupError("setup: %s" % e) from None
    if pair["A"] == pair["B"]:
        raise SetupError("both panels hold the same task %d" % pair["A"])
    return pair


def focus_by_touch(ctx: Ctx, side: str, task: int, others, name: str) -> tuple[Verdict, list[str], str]:
    """Taps the task's title (not an editor): proves real input receipt and moves focus to it."""
    dev = ctx.dev
    x, y = dev.task_point(task, "title", side)
    st = dev.step(name, lambda: dev.tap(x, y), until=_touched(task))
    v = C.eval_touch(st.events, x, y, task, others)
    v.merge(C.eval_focus(st.snap, task))
    return v, st.evidence, st.marker


def relaunch(ctx: Ctx, side: str, key: str, task: int, name: str) -> Verdict:
    """Reuses the side's task from the probe home (restores a minimized task); the same task id must come back."""
    v, tid, st = launch_from_home(ctx, side, key, name, expect_task=task)
    return v


def restore_pair(ctx: Ctx, pair: dict, keys: dict, name: str, check: Check | None = None) -> None:
    """After Home/Back: relaunch any pair task that is hidden or stopped, so the next repetition starts clean. If a
    visible pair task is not reachable by input at its panel (an animation left it transformed, or its window was
    dropped from input), the evidence is saved and the pair is re-created (`pair` is updated in place): later
    repetitions must not inherit a broken state, and the attempt that caused it already recorded the failure."""
    dev = ctx.dev
    snap = dev.snap(name + "-check", kinds=("activities",))
    acts = snap.activities
    for side in SIDES:
        t = acts.task(pair[side])
        if t is None:
            raise SetupError("task %d (side %s) lost" % (pair[side], side))
        states = {a.state for a in t.activities}
        if t.force_hidden & C.POLICY_HIDDEN or not t.visible or "RESUMED" not in states:
            v = relaunch(ctx, side, keys[side], pair[side], "%s.%s" % (name, side))
            if not v.ok:
                raise SetupError("could not restore side %s: %s" % (side, "; ".join(v.reasons)))
    snap = dev.snap(name + "-input", kinds=("windows", "input"))
    broken = []
    for side in SIDES:
        v = C.eval_input_ok(snap, pair[side], side, dev.geo)
        if not v.ok:
            broken += v.reasons
    if broken:
        files = [dev.save_text(name + "-broken-window", dev.adb.shell("dumpsys window", timeout=60)),
                 dev.save_text(name + "-broken-wmshell", dev.adb.shell(
                     "dumpsys activity service SystemUIService WMShell", timeout=60, check=False))]
        msg = "pair re-created: device left in a broken state (%s); evidence %s" % ("; ".join(broken), ", ".join(files))
        if check is not None and check.attempts:
            check.attempts[-1].notes.append(msg)
        pair.update(setup_pair(ctx, keys, name + ".recreate"))


PAIR_KEYS = {"A": "probeTest", "B": "probeTest2"}


def edge_x(side: str, geo: Geometry, outer: bool = True) -> tuple[int, int]:
    """Start and end x of a horizontal Back swipe from the side's outer (or hinge-side) edge."""
    p = geo.panel(side)
    if side == "A":
        return (p.left + 1, p.left + 450) if outer else (p.right - 1, p.right - 450)
    return (p.right - 2, p.right - 450) if outer else (p.left + 1, p.left + 450)


# ---------------------------------------------------------------------------------------------------------------------
# G1 launch / reuse

def gate_g1(ctx: Ctx) -> None:
    """G1 launch/reuse per panel from the probe home (also launchDisplayId=0), cross-panel reuse, am start --display 0 and a freeform request."""
    dev, n = ctx.dev, ctx.reps
    blocks = [("unset", ctx.check("G1.launch.A", "G1", "Probe home launch/reuse lands in panel A")),
              ("unset", ctx.check("G1.launch.B", "G1", "Probe home launch/reuse lands in panel B")),
              ("0", ctx.check("G1.launch0.A", "G1", "launchDisplayId=0 launch/reuse lands in panel A")),
              ("0", ctx.check("G1.launch0.B", "G1", "launchDisplayId=0 launch/reuse lands in panel B"))]
    toggled = False
    for display_id, chk in blocks:
        side = chk.id[-1]
        if display_id == "0" and not toggled:
            st = None
            try:
                x, y = dev.point(dev.home_targets(), "A.toggle", "A")
                st = dev.step("G1.toggle.on", lambda: dev.tap(x, y), snapshot=False,
                              until=lambda evs: any(e.event == "toggle" for e in evs))
            except EvidenceError:
                pass
            if st is None or not any(e.event == "toggle" and e.kv.get("sendLaunchDisplayId") == "true"
                                     for e in st.events):
                for _, c in blocks[2:]:
                    c.skip("could not switch the probe home's launchDisplayId=0 toggle on")
                break
            toggled = True
        dev.remove_probe_tasks()
        held = {"first": None}
        for i in range(n):
            def attempt():
                v, tid, st = launch_from_home(ctx, side, "probeTest", "%s.%d" % (chk.id, i + 1),
                                              display_id=display_id, expect_task=held["first"])
                held["tid"] = tid
                return v, st.evidence, st.marker
            held["tid"] = None
            _record(chk, attempt)
            tid = held["tid"]
            if held["first"] is None:
                held["first"] = tid
            # Home on this (focused) panel uncovers the probe home for the next tap.
            dev.step("%s.%d.home" % (chk.id, i + 1), lambda: dev.key(KEY_HOME), snapshot=False,
                     until=_stopped(tid) if tid is not None else None)
    if toggled:
        try:
            x, y = dev.point(dev.home_targets(), "A.toggle", "A")
            dev.step("G1.toggle.off", lambda: dev.tap(x, y), snapshot=False,
                     until=lambda evs: any(e.event == "toggle" for e in evs))
        except EvidenceError:
            pass

    cross = ctx.check("G1.crossReuse", "G1", "Probe test reused from the other panel moves to the tapped panel",
                      required=min(5, n))
    dev.remove_probe_tasks()
    v, task, st = launch_from_home(ctx, "A", "probeTest", "G1.cross.start")
    if task is None or not v.ok:
        cross.skip("start launch failed: " + "; ".join(v.reasons))
    else:
        side = "A"
        for i in range(cross.required):
            dev.step("G1.cross.%d.home" % (i + 1), lambda: dev.key(KEY_HOME), snapshot=False, until=_stopped(task))
            side = other(side)

            def attempt(side=side):
                v, tid, st = launch_from_home(ctx, side, "probeTest", "G1.cross.%d" % (i + 1), expect_task=task)
                return v, st.evidence, st.marker
            _record(cross, attempt)

    disp0 = ctx.check("G1.amStartDisplay0", "G1", "am start --display 0 (not from home) keeps the platform choice "
                      "(default task area)", required=1)
    dev.remove_probe_tasks()

    def attempt_display0():
        st = dev.step("G1.display0", lambda: dev.adb.shell(
            "am start --display 0 -n org.duode.probe/.ProbeTestActivity"), until=_resumed("ProbeTestActivity"))
        tasks = sorted({e.task for e in st.events if e.event == "onResume" and e.act_class == "ProbeTestActivity"})
        if len(tasks) != 1:
            return Verdict().incomplete("expected one resumed probe task, got %s" % tasks), st.evidence, st.marker
        return C.check_default_area(st.snap, tasks[0], dev.geo), st.evidence, st.marker
    _record(disp0, attempt_display0)

    ff = ctx.check("G1.freeformRequest", "G1", "am start --windowingMode 5 (freeform request) in panel mode: recorded "
                   "(policy undefined until M2.5)", required=1, manual=True)
    dev.remove_probe_tasks()

    def attempt_freeform():
        st = dev.step("G1.freeform", lambda: dev.adb.shell(
            "am start --windowingMode 5 -n org.duode.probe/.ProbeTestActivity"), until=_resumed("ProbeTestActivity"))
        v = Verdict()
        tasks = sorted({e.task for e in st.events if e.event == "onResume" and e.act_class == "ProbeTestActivity"})
        for t in tasks:
            node = st.snap.containers.task(t)
            tda = st.snap.containers.task_display_area(node) if node else None
            v.note("task %d: %s mode=%s bounds=%s" % (t, tda.name if tda else None, node and node.mode,
                                                     node and node.bounds))
        if not tasks:
            v.incomplete("probe did not resume")
        return v, st.evidence, st.marker
    _record(ff, attempt_freeform)
    dev.remove_probe_tasks()


# ---------------------------------------------------------------------------------------------------------------------
# G1L Launcher3 path (operator taps, machine-verified outcome and tap side)

def gate_g1_launcher3(ctx: Ctx) -> None:
    """G1 Launcher3 path: operator taps an icon on each panel; placement, reuse and tap side machine-verified."""
    dev, op, n = ctx.dev, ctx.op, ctx.reps
    pkg = ctx.options.get("launcher_app", "com.android.calendar")
    quickstep = ctx.options.get("quickstep", "com.android.launcher3/.uioverrides.QuickstepLauncher")
    # operator-performed taps: machine-verified (placement, reuse, tap side) but never an automated PASS
    chks = {s: ctx.check("G1L.%s" % s, "G1L", "Launcher3 icon tap (%s) lands in panel %s" % (pkg, s), manual=True)
            for s in SIDES}
    if not op.interactive:
        for c in chks.values():
            c.skip("needs an operator (non-interactive run)")
        return
    dev.set_home(quickstep)
    try:
        for side in SIDES:
            first = None
            for i in range(n):
                def attempt(side=side, i=i):
                    nonlocal first
                    mid, start = dev.mark("G1L.%s.%d" % (side, i + 1))
                    if not op.wait("On the %s panel (%s), tap the %s icon in Launcher3."
                                   % ("LEFT" if side == "A" else "RIGHT", side, pkg)):
                        raise SetupError("operator skipped")
                    time.sleep(dev.quiet)
                    end = dev.end(mid)
                    snap = dev.snap(mid + "-G1L")
                    v = Verdict()
                    tasks = snap.containers.tasks_with_component(pkg + "/")
                    if len(tasks) != 1:
                        return v.incomplete("%d tasks of %s (expected exactly one)" % (len(tasks), pkg)), \
                            snap.files, mid
                    tid = tasks[0]
                    acts = snap.activities.task(tid)
                    src = next((a.source_bounds for a in (acts.activities if acts else []) if a.source_bounds), None)
                    if src is None:
                        v.incomplete("no source bounds on the launch intent of task %d" % tid)
                    elif not dev.geo.panel(side).contains(src):
                        v.incomplete("the icon tapped was at %s, not on panel %s (operator error)" % (src, side))
                    if first is not None and tid != first:
                        v.fail("reuse expected task %d, got %d" % (first, tid))
                    first = first or tid
                    v.merge(C.check_placement(snap, tid, side, dev.geo))
                    v.merge(C.check_not_policy_hidden(snap, tid))
                    dev.step("G1L.%s.%d.home" % (side, i + 1), lambda: dev.key(KEY_HOME), snapshot=False)
                    return v, snap.files, mid
                _record(chks[side], attempt)
    finally:
        dev.set_home(PROBE_HOME)


# ---------------------------------------------------------------------------------------------------------------------
# G2 independent Home

def gate_g2(ctx: Ctx) -> None:
    """G2 independent Home: focused Home per panel, other panel keeps task/visibility/input; restore; cross-focused Home gesture."""
    dev, op, n = ctx.dev, ctx.op, ctx.reps
    home = {s: ctx.check("G2.home.%s" % s, "G2", "Home on focused panel %s minimizes only %s; the other panel keeps "
                         "its task, visibility and input" % (s, s)) for s in SIDES}
    restore = {s: ctx.check("G2.restore.%s" % s, "G2", "Relaunch after Home on %s restores the same task" % s)
               for s in SIDES}
    cross = {s: ctx.check("G2.crossHome.%s" % s, "G2", "Home gesture on panel %s while the other panel is focused "
                          "(no preparatory focus tap) minimizes only %s" % (s, s)) for s in SIDES}
    try:
        pair = setup_pair(ctx, PAIR_KEYS, "G2.setup")
    except SetupError as e:
        for c in list(home.values()) + list(restore.values()) + list(cross.values()):
            c.skip(str(e))
        return

    for s in SIDES:
        o = other(s)
        for i in range(n):
            tag = "G2.home.%s.%d" % (s, i + 1)

            def attempt(s=s, o=o, tag=tag):
                v, ev, _ = focus_by_touch(ctx, s, pair[s], (pair[o],), tag + ".focus")
                st = dev.step(tag, lambda: dev.key(KEY_HOME), until=_stopped(pair[s], pair[o]))
                v.merge(C.eval_home(st.lines, st.events, st.snap, s, pair[s], pair[o], dev.geo))
                v2, ev2, _ = focus_by_touch(ctx, o, pair[o], (pair[s],), tag + ".otherInput")
                v.merge(v2)
                return v, ev + st.evidence + ev2, st.marker
            _record(home[s], attempt)

            def attempt_restore(s=s, tag=tag):
                v, tid, st = launch_from_home(ctx, s, PAIR_KEYS[s], tag + ".relaunch", expect_task=pair[s])
                v.merge(C.eval_restored(st.lines, pair[s]))
                return v, st.evidence, st.marker
            _record(restore[s], attempt_restore)
            restore_pair(ctx, pair, PAIR_KEYS, tag + ".clean", restore[s])

    manual: dict = {}

    def manual_check(s):
        if s not in manual:
            manual[s] = ctx.check("G2.crossHomeManual.%s" % s, "G2", "Real Home gesture on panel %s while the other "
                                  "panel is focused (operator fallback when the synthetic swipe is not recognized)"
                                  % s, required=1, manual=True)
        return manual[s]

    for s in SIDES:
        o = other(s)
        p = dev.geo.panel(s)
        x = (p.left + p.right) // 2
        for i in range(n):
            tag = "G2.cross.%s.%d" % (s, i + 1)
            held = {"unrecognized": False}

            def attempt(s=s, o=o, tag=tag, held=held):
                v, ev, _ = focus_by_touch(ctx, o, pair[o], (pair[s],), tag + ".focusOther")
                st = dev.step(tag, lambda: dev.swipe(x, p.bottom - 3, x, p.bottom - 700, 250),
                              until=_stopped(pair[s], pair[o]))
                hv = C.eval_home(st.lines, st.events, st.snap, s, pair[s], pair[o], dev.geo)
                hv.note("Home by synthetic swipe")
                held["unrecognized"] = not any(C.policy_minimized_panel(l) for l in st.lines)
                v.merge(hv)
                return v, ev + st.evidence, st.marker
            _record(cross[s], attempt)

            if held["unrecognized"] and op.interactive:
                # The automated attempt stays as recorded (INCOMPLETE); the operator's real gesture is a separate,
                # manual check: it can FAIL on contradicting evidence but never PASS.
                restore_pair(ctx, pair, PAIR_KEYS, tag + ".manual.clean")

                def attempt_manual(s=s, o=o, tag=tag):
                    v, ev, _ = focus_by_touch(ctx, o, pair[o], (pair[s],), tag + ".manual.focusOther")
                    mid, start = dev.mark(tag + ".manual")
                    if not op.wait("Swipe up from the bottom edge of the %s panel (%s) to go Home. Do NOT tap that "
                                   "panel first." % ("LEFT" if s == "A" else "RIGHT", s)):
                        raise SetupError("operator skipped")
                    dev.log.wait_for(start, _stopped(pair[s], pair[o]), dev.settle)
                    time.sleep(dev.quiet)
                    end = dev.end(mid)
                    snap = dev.snap(tag + ".manual")
                    v.merge(C.eval_home(dev.log.between(start, end), dev.log.events(start, end), snap, s, pair[s],
                                        pair[o], dev.geo))
                    v.note("Home performed by the operator (real gesture)")
                    return v, ev + ["marker " + mid] + snap.files, mid
                _record(manual_check(s), attempt_manual)
            restore_pair(ctx, pair, PAIR_KEYS, tag + ".clean", cross[s])


# ---------------------------------------------------------------------------------------------------------------------
# G4 Back / focus

def gate_g4(ctx: Ctx) -> None:
    """G4 Back from each outer edge with the other panel focused; guided hinge-edge Back and predictive-Back cancel."""
    dev, op, n = ctx.dev, ctx.op, ctx.reps
    back = {s: ctx.check("G4.back.%s" % s, "G4", "Back from the outer edge of %s acts on %s while the other panel is "
                         "focused" % (s, s)) for s in SIDES}
    hinge = {s: ctx.check("G4.hingeBack.%s" % s, "G4", "Back from the hinge-side edge of %s (real gesture)" % s,
                          required=1, manual=True) for s in SIDES}
    cancel = {s: ctx.check("G4.backCancel.%s" % s, "G4", "Predictive Back cancelled on %s: no lifecycle change, "
                           "animation within the panel (real gesture)" % s, required=1, manual=True) for s in SIDES}
    if ctx.pre.get("navigation_mode") != "2":
        for c in back.values():
            c.skip("navigation mode %s is not gestural (2)" % ctx.pre.get("navigation_mode"))
    try:
        pair = setup_pair(ctx, PAIR_KEYS, "G4.setup")
    except SetupError as e:
        for c in list(back.values()) + list(hinge.values()) + list(cancel.values()):
            c.skip(str(e))
        return

    for s in SIDES:
        if back[s].skipped:
            break
        o = other(s)
        x1, x2 = edge_x(s, dev.geo)
        for i in range(n):
            tag = "G4.back.%s.%d" % (s, i + 1)

            def attempt(s=s, o=o, tag=tag):
                v, ev, _ = focus_by_touch(ctx, o, pair[o], (pair[s],), tag + ".focusOther")
                st = dev.step(tag, lambda: dev.swipe(x1, 900, x2, 900, 300), until=_stopped(pair[s], pair[o]))
                v.merge(C.eval_back(st.events, st.snap, s, pair[s], pair[o], dev.geo))
                return v, ev + st.evidence, st.marker
            _record(back[s], attempt)
            restore_pair(ctx, pair, PAIR_KEYS, tag + ".clean", back[s])

    for s in SIDES:
        o = other(s)
        if not op.interactive:
            hinge[s].skip("needs an operator")
            cancel[s].skip("needs an operator")
            continue

        def attempt_hinge(s=s, o=o):
            v, ev, _ = focus_by_touch(ctx, o, pair[o], (pair[s],), "G4.hinge.%s.focusOther" % s)
            mid, start = dev.mark("G4.hinge.%s" % s)
            x = dev.geo.panel(s).right - 1 if s == "A" else dev.geo.panel(s).left
            if not op.wait("Swipe Back from the HINGE-side edge of the %s panel (x~%d), inward, then release."
                           % ("LEFT" if s == "A" else "RIGHT", x)):
                raise SetupError("operator skipped")
            dev.log.wait_for(start, _stopped(pair[s], pair[o]), dev.settle)
            end = dev.end(mid)
            snap = dev.snap(mid + "-hinge", screenshot=True)
            v.merge(C.eval_back(dev.log.events(start, end), snap, s, pair[s], pair[o], dev.geo))
            seen = op.yes_no("Did a Back animation start on the %s panel only?" % ("LEFT" if s == "A" else "RIGHT"))
            v.note("operator: back animation on panel only = %s" % seen)
            if seen is False:
                v.note("operator saw no Back on the hinge edge (record as behaviour, see plan F15)")
            return v, ev + ["marker " + mid] + snap.files, mid
        _record(hinge[s], attempt_hinge)
        restore_pair(ctx, pair, PAIR_KEYS, "G4.hinge.%s.clean" % s)

        def attempt_cancel(s=s, o=o):
            mid, start = dev.mark("G4.cancel.%s" % s)
            if not op.wait("On the %s panel: start a Back swipe from its OUTER edge, hold until the preview shrinks, "
                           "move back to the edge and release (cancel)." % ("LEFT" if s == "A" else "RIGHT")):
                raise SetupError("operator skipped")
            time.sleep(dev.quiet)
            end = dev.end(mid)
            snap = dev.snap(mid + "-cancel", screenshot=True)
            v = Verdict()
            for e in dev.log.events(start, end):
                if e.event in ("onStop", "onDestroy") and e.task in pair.values():
                    v.fail("cancelled Back changed lifecycle: task %d %s" % (e.task, e.event))
            v.merge(C.eval_invariants(snap, {pair[x]: x for x in SIDES}, expect_sides=True, geo=dev.geo))
            inside = op.yes_no("Did the predictive Back animation stay within the %s panel?"
                               % ("LEFT" if s == "A" else "RIGHT"))
            if inside is False:
                v.fail("operator: predictive Back animation left the panel")
            v.note("operator: animation within panel = %s" % inside)
            return v, ["marker " + mid] + snap.files, mid
        _record(cancel[s], attempt_cancel)
        restore_pair(ctx, pair, PAIR_KEYS, "G4.cancel.%s.clean" % s)


# ---------------------------------------------------------------------------------------------------------------------
# G5 IME

def gate_g5(ctx: Ctx) -> None:
    """G5 IME: editor focus switches A/B, keyboard panel, IME targets, insets, typed sentinel, other panel touchable, rotation; guided fold."""
    dev, op, n = ctx.dev, ctx.op, ctx.reps
    seq = [("A", 1, "G5.ime.A.editor1", "first editor on A"),
           ("A", 2, "G5.ime.A.editor2", "switch to the second editor on A"),
           ("B", 1, "G5.ime.B.fromA", "A -> B editor focus change"),
           ("A", 1, "G5.ime.A.fromB", "B -> A editor focus change with one tap (F10)")]
    chks = {cid: ctx.check(cid, "G5", "IME: %s (focus, keyboard panel, IME targets, insets only for the target, "
                           "typed sentinel reaches the target only)" % title) for _, _, cid, title in seq}
    touch = ctx.check("G5.otherTouchable", "G5", "Other panel takes a tap while the keyboard is up")
    rot = ctx.check("G5.rotate", "G5", "Rotate with the keyboard up: keyboard stays in the target panel, tasks keep "
                    "their panels", required=1)
    fold = ctx.check("G5.foldKeyboard", "G5", "Fold to one panel with the keyboard up (real fold)", required=1,
                     manual=True)
    # show_ime_with_hard_keyboard is left at the device's value (recorded in the preconditions): forcing it could mask
    # IME defects. A keyboard that does not show on the first tap is a G5 failure, not a reason to skip.
    try:
        pair = setup_pair(ctx, PAIR_KEYS, "G5.setup")
    except SetupError as e:
        for c in list(chks.values()) + [touch, rot, fold]:
            c.skip(str(e))
        return

    def latest(task, event, before, editor=None):
        return dev.log.latest(lambda e: e.task == task and e.event == event
                              and (editor is None or e.kv.get("editor") == str(editor)), before=before)

    def ime_up(e):
        return e is not None and e.kv.get("imeVisible") == "true" and not e.kv.get("ime", "").endswith(",0]")

    for i in range(n):
        for side, editor, cid, _ in seq:
            o = other(side)
            tag = "%s.%d" % (cid, i + 1)

            def attempt(side=side, editor=editor, o=o, tag=tag):
                t = pair[side]
                x, y = dev.task_point(t, "editor%d" % editor, side)
                if ime_up(latest(t, "insets", None)):
                    # keyboard already up for this task: the tap only moves editor focus
                    until = lambda evs: any(e.task == t and (e.event == "editorFocus" or (
                        e.event == "touch" and e.kv.get("action") == "up")) for e in evs)
                else:
                    until = lambda evs: any(e.task == t and e.event == "insets" and ime_up(e) for e in evs)
                st = dev.step(tag, lambda: dev.tap(x, y), until=until)
                v = C.eval_touch(st.events, x, y, t, (pair[o],))
                v.merge(C.eval_ime(st.snap, side, editor, t, pair[o],
                                   target_focus=latest(t, "editorFocus", st.end, editor),
                                   target_insets=latest(t, "insets", st.end),
                                   other_insets=latest(pair[o], "insets", st.end), geo=dev.geo))
                token = dev.sentinel()
                tt = dev.step(tag + ".type", lambda: dev.type_token(token), snapshot=False,
                              until=lambda evs: any(token in e.kv.get("sentinels", "") for e in evs))
                v.merge(C.eval_text(tt.events, token, t, (pair[o],)))
                return v, st.evidence + ["marker " + tt.marker], st.marker
            _record(chks[cid], attempt)

        def attempt_touch(i=i):
            # keyboard is up on A after the sequence; the other panel must still take a tap
            return focus_by_touch(ctx, "B", pair["B"], (pair["A"],), "G5.otherTouchable.%d" % (i + 1))
        _record(touch, attempt_touch)

    def attempt_rotate():
        # The probe has no configChanges, so a rotation relaunches it and drops the keyboard; the check is that the
        # keyboard brought up again in the rotated layout is confined to the panel area holding the target task,
        # and that both tasks keep their panel areas. Rotation with a surviving IME target (configChanges app,
        # frameworks 0015) is not covered by the probe.
        t, o = pair["A"], pair["B"]

        def rotated(r):
            return lambda evs: any(e.task == t and e.kv.get("rotation") == str(r) for e in evs) and any(
                e.task == t and e.event == "targets" for e in evs)
        rotated_step = dev.step("G5.rotate", lambda: dev.adb.put_setting("system", "user_rotation", "1"),
                                until=rotated(1), timeout=2 * dev.settle)
        v = C.eval_rotation(rotated_step.snap, 1, rotated_step.settled)
        if not v.ok:
            dev.adb.put_setting("system", "user_rotation", "0")
            return v, rotated_step.evidence, rotated_step.marker
        c = rotated_step.snap.containers
        node = c.task(t)
        area = c.task_display_area(node) if node else None
        if area is None:
            raise EvidenceError("task %d not found after rotation" % t)
        x, y = dev.task_point(t, "editor1", area=area.bounds)
        st = dev.step("G5.rotate.ime", lambda: dev.tap(x, y), until=lambda evs: any(
            e.task == t and e.event == "insets" and ime_up(e) for e in evs))
        v.merge(C.eval_touch(st.events, x, y, t, (o,)))
        v.merge(C.eval_ime_rotated(st.events, st.snap, t, o))
        back = dev.step("G5.rotate.back", lambda: dev.adb.put_setting("system", "user_rotation", "0"),
                        until=rotated(0), timeout=2 * dev.settle)
        v.merge(C.eval_rotation(back.snap, 0, back.settled))
        v.merge(C.eval_invariants(back.snap, {t: "A", o: "B"}, expect_sides=True, geo=dev.geo))
        return v, rotated_step.evidence + st.evidence + back.evidence, st.marker
    _record(rot, attempt_rotate)

    if not op.interactive:
        fold.skip("needs an operator")
        return

    def attempt_fold():
        t, o = pair["A"], pair["B"]
        x, y = dev.task_point(t, "editor1", "A")
        dev.step("G5.fold.prepare", lambda: dev.tap(x, y), snapshot=False, until=lambda evs: any(
            e.task == t and e.event == "insets" and e.kv.get("imeVisible") == "true" for e in evs))
        mid, start = dev.mark("G5.fold")
        if not op.wait("With the keyboard up on the LEFT panel, fold the RIGHT panel back (left-only posture)."):
            raise SetupError("operator skipped")
        time.sleep(3)
        end = dev.end(mid)
        snap = dev.snap(mid + "-fold", screenshot=True)
        v = C.eval_invariants(snap, {t: "A", o: "B"}, geo=dev.geo)
        ok = op.yes_no("Is the keyboard fully on the lit panel and typing into the left probe's editor?")
        if ok is False:
            v.fail("operator: keyboard not usable on the lit panel after the fold")
        op.wait("Unfold back to both panels.")
        time.sleep(3)
        after = dev.snap("G5.fold.unfolded", screenshot=True)
        v.merge(C.eval_invariants(after, {t: "A", o: "B"}, expect_sides=True, geo=dev.geo))
        return v, ["marker " + mid] + snap.files + after.files, mid
    _record(fold, attempt_fold)


# ---------------------------------------------------------------------------------------------------------------------
# G6 authentication safety

def _unlock_with_pin(ctx: Ctx, pin: str, tag: str) -> None:
    """Shows the bouncer and types the PIN, but only into SystemUI: refuses if an app window has focus."""
    dev = ctx.dev
    dev.adb.shell("wm dismiss-keyguard", check=False)
    time.sleep(1.2)
    snap = dev.snap(tag + ".bouncer", kinds=("wdisplays", "windows"))
    focus = snap.wdisplays.current_focus
    win = snap.windows.get(focus.hash) if focus else None
    if win is None or win.package != "com.android.systemui" or win.task_id not in (None, -1):
        raise SetupError("refusing to type the PIN: focus is %s" % (focus.title if focus else None))
    dev.type_secret(pin)
    dev.key(KEY_ENTER)


def gate_g6(ctx: Ctx) -> None:
    """G6 authentication safety with a secure lock: lock/wake cycles with ordinary and showWhenLocked tasks, attempted taps, unlock."""
    dev, op, n = ctx.dev, ctx.op, ctx.reps
    phases = [("ordinary", {"A": "probeTest", "B": "probeTest2"}, None),
              ("swlA", {"A": "probeLocked", "B": "probeTest2"}, "A"),
              ("swlB", {"A": "probeTest", "B": "probeLocked"}, "B")]
    mode = ctx.options.get("g6_unlock", "pin")
    checks = {}
    for name, keys, swl in phases:
        checks[name] = (
            ctx.check("G6.locked.%s" % name, "G6", "Locked (%s): keyguard covers both panels; no ordinary content "
                      "visible, focused or touchable; attempted taps not delivered" % name),
            # an operator unlock is a manual action: its success is never an automated PASS
            ctx.check("G6.unlock.%s" % name, "G6", "Unlock (%s): keyguard gone, tasks kept ids and panels" % name,
                      manual=(mode == "manual")),
            ctx.check("G6.occlusion.%s" % name, "G6", "showWhenLocked task occludes the keyguard on its panel (F8, "
                      "functional)") if swl else None)
    cred = ctx.pre.get("secure_lock")
    reason = None
    if cred not in ("PIN", "PASSWORD", "PATTERN"):
        reason = "no secure screen lock (credential %s): set a PIN on the device first" % cred
    elif mode == "pin" and cred == "PATTERN":
        reason = "pattern lock cannot be typed: use --g6-unlock manual"
    elif not op.interactive:
        reason = "needs an operator (PIN entry or manual unlock)"
    pin = None
    if reason is None and mode == "pin":
        pin = op.secret("Screen lock %s for G6 (typed into the keyguard by adb only)" % cred)
        if not pin or not pin.isalnum():
            reason = "no usable PIN entered (letters and digits only); use --g6-unlock manual"
    if reason:
        for trio in checks.values():
            for c in trio:
                if c:
                    c.skip(reason)
        return

    try:
        for name, keys, swl in phases:
            locked, unlocked, occl = checks[name]
            try:
                pair = setup_pair(ctx, keys, "G6.%s.setup" % name)
            except SetupError as e:
                for c in (locked, unlocked, occl):
                    if c:
                        c.skip(str(e))
                continue
            ordinary = tuple(pair[s] for s in SIDES if s != swl)
            swl_task = pair[swl] if swl else None
            touch_side = next(s for s in SIDES if s != swl) if swl else "B"
            tx, ty = dev.task_point(pair[touch_side], "title", touch_side)
            for i in range(n):
                tag = "G6.%s.%d" % (name, i + 1)

                def lock_action():
                    dev.key(KEY_POWER)
                    time.sleep(1.5)
                    dev.key(KEY_WAKEUP)
                    time.sleep(1.5)
                    dev.tap(tx, ty)          # attempted interaction with ordinary content while locked
                st = dev.step(tag + ".locked", lock_action, screenshot=True)

                def attempt_locked():
                    return C.eval_locked(st.events, st.snap, ordinary, swl_task), st.evidence, st.marker
                _record(locked, attempt_locked)
                if occl:
                    _record(occl, lambda: (C.eval_occlusion(st.snap, swl_task), st.evidence, st.marker))

                def attempt_unlock():
                    if mode == "pin":
                        u = dev.step(tag + ".unlock", lambda: _unlock_with_pin(ctx, pin, tag), snapshot=False)
                        time.sleep(2)
                    else:
                        mid, start = dev.mark(tag + ".unlock")
                        op.wait("Unlock the device on screen with your PIN/pattern.")
                        dev.end(mid)
                    snap = dev.snap(tag + ".unlocked", screenshot=True)
                    v = C.eval_unlocked(snap, {pair[s]: s for s in SIDES}, dev.geo)
                    return v, snap.files, None
                v = _record(unlocked, attempt_unlock)
                if v.status != PASS:
                    # never retry a PIN blindly (lockout); hand over to the operator
                    op.wait("G6 stopped: the device did not unlock cleanly. Unlock it by hand if needed.")
                    return
    finally:
        pin = None


# ---------------------------------------------------------------------------------------------------------------------
# G3 Recents (guided)

def gate_g3(ctx: Ctx) -> None:
    """G3 Recents (guided, manual): open/select/cancel Overview from each panel with the probe HOME and Quickstep."""
    dev, op = ctx.dev, ctx.op
    homes = [("probe", PROBE_HOME)]
    if ctx.options.get("quickstep"):
        homes.append(("quickstep", ctx.options["quickstep"]))
    chks = {}
    for hname, _ in homes:
        for s in SIDES:
            chks[(hname, s, "open")] = ctx.check("G3.%s.open.%s" % (hname, s), "G3", "Overview opened from %s (%s "
                                                 "HOME) is visible and interactive" % (s, hname), 1, manual=True)
            chks[(hname, s, "select")] = ctx.check("G3.%s.select.%s" % (hname, s), "G3", "Selecting the other "
                                                   "panel's task from %s's Overview puts it on %s (invoking panel); the "
                                                   "other task is kept" % (s, s), 1, manual=True)
            chks[(hname, s, "cancel")] = ctx.check("G3.%s.cancel.%s" % (hname, s), "G3", "Cancelling Overview "
                                                   "opened from %s keeps both tasks in place" % s, 1, manual=True)
    if not op.interactive:
        for c in chks.values():
            c.skip("needs an operator (real Recents gestures)")
        return
    try:
        for hname, comp in homes:
            dev.set_home(PROBE_HOME)
            pair = setup_pair(ctx, PAIR_KEYS, "G3.%s.setup" % hname)
            if comp != PROBE_HOME:
                dev.set_home(comp)
            for s in SIDES:
                o = other(s)
                side_name = "LEFT" if s == "A" else "RIGHT"

                def guided(key, msg, evaluate):
                    def attempt():
                        mid, start = dev.mark("G3.%s.%s.%s" % (hname, key, s))
                        if not op.wait(msg):
                            raise SetupError("operator skipped")
                        time.sleep(dev.quiet)
                        end = dev.end(mid)
                        snap = dev.snap(mid + "-G3", screenshot=True)
                        v = evaluate(snap, dev.log.events(start, end))
                        return v, ["marker " + mid] + snap.files, mid
                    _record(chks[(hname, s, key)], attempt)

                def ev_open(snap, events):
                    v = C.eval_invariants(snap, {pair["A"]: "A", pair["B"]: "B"}, geo=dev.geo)
                    ok = op.yes_no("Is Overview visible and does it respond to touch?")
                    if ok is False:
                        v.fail("operator: Overview not visible/interactive")
                    scope = op.choose("Overview covers which area?", ["panel", "display"])
                    v.note("operator: Overview scope = %s (whole-display is a provisional prototype result)" % scope)
                    return v
                guided("open", "Open Overview from the %s panel: swipe up from its bottom edge and hold." % side_name,
                       ev_open)

                def ev_select(snap, events):
                    v = C.eval_invariants(snap, {pair["A"]: "A", pair["B"]: "B"}, geo=dev.geo)
                    v.merge(C.check_placement(snap, pair[o], s, dev.geo))
                    return v
                guided("select", "In Overview, select the task that was on the %s panel."
                       % ("RIGHT" if s == "A" else "LEFT"), ev_select)
                # start the cancel step from a clean pair again
                dev.set_home(PROBE_HOME)
                pair = setup_pair(ctx, PAIR_KEYS, "G3.%s.%s.reset" % (hname, s))
                if comp != PROBE_HOME:
                    dev.set_home(comp)

                def ev_cancel(snap, events):
                    return C.eval_invariants(snap, {pair["A"]: "A", pair["B"]: "B"}, expect_sides=True, geo=dev.geo)
                guided("cancel", "Open Overview from the %s panel again, then cancel it (swipe down or Back)."
                       % side_name, ev_cancel)
    except SetupError as e:
        for c in chks.values():
            if len(c.attempts) < c.required and c.skipped is None:
                c.skip(str(e))
    finally:
        dev.set_home(PROBE_HOME)


# ---------------------------------------------------------------------------------------------------------------------
# G7 physical posture (guided) + scripted rotation

def gate_g7(ctx: Ctx) -> None:
    """G7 posture: scripted rotations in dual posture; guided physical folds per side; Home-fold-relaunch (P1b)."""
    dev, op, n = ctx.dev, ctx.op, ctx.reps
    fold = {s: ctx.check("G7.fold.%s" % s, "G7", "Fold to %s-only and unfold: no task lost, no stranded hidden flag, "
                         "tasks back on their panels; lit panel shows its own task" % s, manual=True) for s in SIDES}
    homefold = ctx.check("G7.homeFoldRelaunch", "G7", "Home on B, fold to A-only, relaunch B's app: visible, resumed, "
                         "restored (P1b)", 1, manual=True)
    rot = ctx.check("G7.rotate", "G7", "Rotation 90/180/270 and back to 0 in dual posture (display rotation verified): "
                    "tasks keep their panel areas, no overlap, back on their panels at 0", required=4)
    try:
        pair = setup_pair(ctx, PAIR_KEYS, "G7.setup")
    except SetupError as e:
        for c in list(fold.values()) + [homefold, rot]:
            c.skip(str(e))
        return
    tasks = {pair["A"]: "A", pair["B"]: "B"}

    def rotated_to(r):
        """Action-scoped completion: every probe task reported the requested rotation after the marker."""
        return lambda evs: all(any(e.task == t and e.kv.get("rotation") == str(r) for e in evs) for t in tasks)

    for r in (1, 2, 3, 0):
        def attempt(r=r):
            st = dev.step("G7.rotate.%d" % r, lambda: dev.adb.put_setting("system", "user_rotation", str(r)),
                          until=rotated_to(r), timeout=2 * dev.settle)
            v = C.eval_rotation(st.snap, r, st.settled)
            if not v.ok:
                return v, st.evidence, st.marker
            v.merge(C.eval_invariants(st.snap, tasks, expect_sides=(r == 0), geo=dev.geo))
            c = st.snap.containers
            areas = {}
            for tid, side in tasks.items():
                node = c.task(tid)
                tda = c.task_display_area(node) if node else None
                if tda is None or tda.name != Geometry.area(side):
                    v.fail("task %d in %s after rotation %d, expected %s" % (tid, tda and tda.name, r,
                                                                             Geometry.area(side)))
                elif node.bounds != tda.bounds:
                    v.fail("task %d bounds %s differ from its panel %s" % (tid, node.bounds, tda.bounds))
                if tda is not None:
                    areas[side] = tda.bounds
            if len(areas) == 2 and areas["A"].intersects(areas["B"]):
                v.fail("panel areas overlap after rotation %d: %s %s" % (r, areas["A"], areas["B"]))
            return v, st.evidence, st.marker
        _record(rot, attempt)

    if not op.interactive:
        for c in list(fold.values()) + [homefold]:
            c.skip("needs an operator (physical folds)")
        return

    for s in SIDES:
        lit = "LEFT" if s == "A" else "RIGHT"
        dark = "RIGHT" if s == "A" else "LEFT"
        for i in range(n):
            def attempt(s=s, i=i):
                mid, start = dev.mark("G7.fold.%s.%d" % (s, i + 1))
                if not op.wait("Fold the %s panel back so only the %s panel is lit; wait for the screen to settle."
                               % (dark, lit)):
                    raise SetupError("operator skipped")
                time.sleep(2)
                folded = dev.snap(mid + "-folded", screenshot=(i == 0))
                size = dev.adb.shell("wm size").strip()
                dstate = dev.adb.shell("dumpsys device_state | grep -E 'mCommittedState'", check=False).strip()
                v = C.eval_invariants(folded, tasks, geo=dev.geo)
                comps = [C_ for C_ in (dev.log.between(start)) if C_.tag == "POSTURE PROCESSOR"
                         and "composition" in C_.msg]
                v.note("folded: %s | %s | %s" % (size.replace("\n", "; "), dstate,
                                                  [l.msg for l in comps][-2:]))
                own = op.yes_no("Does the lit %s panel show the task that was on it before the fold?" % lit)
                if own is False:
                    v.fail("operator: lit panel shows the other panel's task (F6)")
                if not op.wait("Unfold back to both panels; wait for the screen to settle."):
                    raise SetupError("operator skipped")
                time.sleep(2)
                end = dev.end(mid)
                after = dev.snap(mid + "-unfolded", screenshot=(i == 0))
                v.merge(C.eval_invariants(after, tasks, expect_sides=True, geo=dev.geo))
                for e in dev.log.events(start, end):
                    if e.event == "touch" and e.task in tasks:
                        v.note("touch received by task %d during the fold (check it was on the lit panel)" % e.task)
                return v, ["marker " + mid] + folded.files + after.files, mid
            _record(fold[s], attempt)

    def attempt_homefold():
        restore_pair(ctx, pair, PAIR_KEYS, "G7.homefold.clean")
        focus_by_touch(ctx, "B", pair["B"], (pair["A"],), "G7.homefold.focusB")
        st = dev.step("G7.homefold.home", lambda: dev.key(KEY_HOME), until=_stopped(pair["B"]))
        v = C.eval_home(st.lines, st.events, st.snap, "B", pair["B"], pair["A"], dev.geo)
        mid, start = dev.mark("G7.homefold.relaunch")
        if not op.wait("Fold the RIGHT panel back (left only lit). Then, on the probe home, tap 'Probe test 2 (own "
                       "task)'."):
            raise SetupError("operator skipped")
        dev.log.wait_for(start, _resumed("ProbeOtherTestActivity", pair["B"]), dev.settle)
        time.sleep(dev.quiet)
        end = dev.end(mid)
        snap = dev.snap(mid + "-relaunched", screenshot=True)
        resumed = [e for e in dev.log.events(start, end) if e.event == "onResume" and e.task == pair["B"]]
        if not resumed:
            v.fail("task %d not resumed after the relaunch" % pair["B"])
        v.merge(C.eval_restored(dev.log.between(start, end), pair["B"]))
        v.merge(C.check_not_policy_hidden(snap, pair["B"]))
        op.wait("Unfold back to both panels.")
        return v, st.evidence + ["marker " + mid] + snap.files, mid
    _record(homefold, attempt_homefold)


# ---------------------------------------------------------------------------------------------------------------------
# Panel-off control (reboots twice)

def gate_panel_off(ctx: Ctx) -> None:
    """Panel-off control: clear both panel properties, reboot, check platform behaviour, restore, reboot (needs --allow-reboot)."""
    dev, adb = ctx.dev, ctx.dev.adb
    off = ctx.check("PO.panelOff", "PO", "Both panel properties off + reboot: no panel areas, sys.duode.panels.active=0",
                    required=1)
    launch = {s: ctx.check("PO.launch.%s" % s, "PO", "Panel off: probe launch from side %s is fullscreen in the "
                           "default task area" % s, required=1) for s in SIDES}
    back_on = ctx.check("PO.restored", "PO", "Recorded panel properties restored + reboot: panel mode active again",
                        required=1)
    if not ctx.options.get("allow_reboot"):
        for c in [off, back_on] + list(launch.values()):
            c.skip("needs --allow-reboot")
        return
    recorded = {k: ctx.pre["panels"].get(k, "") for k in ("persist.sys.phh.duo.panels",
                                                           "persist.debug.phh.duo.panels")}
    # SystemProperties.getBoolean semantics (true/yes/on/y/1); an unparsable value is cleared and restored too
    changed = [k for k, val in recorded.items() if android_bool(val) is not False]

    def reboot():
        adb.run(["reboot"], check=False)
        time.sleep(dev.boot_wait)
        adb.wait_for_boot()
        time.sleep(dev.boot_wait)
        dev.restart_log()
        # the keyguard comes back after every boot: dismissed when there is no secure lock (as in the preflight,
        # recorded, nothing restored); a secure lock needs the operator (first unlock after boot)
        state: dict = {"secure_lock": ctx.pre.get("secure_lock")}
        kv = preconditions.check_keyguard(dev, state, dismiss=True)
        ctx.pre.setdefault("keyguard_after_reboot", []).append(state.get("keyguard"))
        if not kv.ok and ctx.pre.get("secure_lock") != "NONE":
            ctx.op.wait("The device rebooted and is locked: unlock it on screen.")
            time.sleep(2)
            kv = preconditions.check_keyguard(dev, state, dismiss=False)
        if not kv.ok:
            raise SetupError("after reboot: %s" % "; ".join(kv.reasons))

    def skip_pending(checks, why):
        for c in checks:
            if not c.attempts and c.skipped is None:
                c.skip(why)

    try:
        for k in changed:
            adb.shell("setprop %s 0" % k, check=False)
        props = {k: adb.getprop(k) for k in recorded}
        if any(android_bool(v_) is not False for v_ in props.values()):
            for c in [off] + list(launch.values()):
                c.skip("cannot clear %s (persist.sys.* needs adb root)" % props)
        else:
            try:
                reboot()
            except SetupError as e:
                skip_pending([off] + list(launch.values()), str(e))
                raise

            def attempt_off():
                snap = dev.snap("PO.off", screenshot=True)
                return C.eval_panel_off(snap, dev.props()), snap.files, None
            _record(off, attempt_off)
            dev.remove_probe_tasks()
            for s in SIDES:
                def attempt(s=s):
                    v, tid, st = launch_from_home(ctx, s, "probeTest", "PO.launch.%s" % s, area="default")
                    return v, st.evidence, st.marker
                _record(launch[s], attempt)
                dev.remove_probe_tasks()
    except SetupError:
        pass        # the pending checks were skipped with the reason; restoration still runs below
    finally:
        for k in changed:
            adb.shell("setprop %s %s" % (k, recorded[k]), check=False)
        if changed:
            try:
                reboot()
            except SetupError as e:
                skip_pending([back_on], str(e))
    if back_on.skipped is not None:
        return

    def attempt_back():
        v = Verdict()
        for k, val in recorded.items():
            if adb.getprop(k) != val:
                v.fail("%s=%r, recorded %r" % (k, adb.getprop(k), val))
        if any(android_bool(val) is True for val in recorded.values()):
            snap = dev.snap("PO.restored", kinds=("containers",))
            v.merge(C.check_geometry(snap, dev.geo))
            return v, snap.files, None
        return v, [], None
    _record(back_on, attempt_back)


# ---------------------------------------------------------------------------------------------------------------------
# G9 stability (evaluated over the whole session)

def gate_g9(ctx: Ctx) -> None:
    """G9 stability: no system_server/SystemUI crash or ANR during the run (dropbox + captured log)."""
    dev = ctx.dev
    chk = ctx.check("G9.noCrash", "G9", "No system_server / SystemUI crash or ANR during the run", required=1)

    def attempt():
        if not dev.log.alive:
            return Verdict().incomplete("logcat capture died during the run"), [], None
        entries = []
        files = []
        for tag in C.SYSTEM_SERVER_TAGS + C.SYSTEM_APP_TAGS:
            text = dev.adb.shell("dumpsys dropbox --print %s" % tag, timeout=60)
            entries += parse_dropbox(text)
            files.append(dev.save_text("dropbox-" + tag, text))
        v = C.eval_crashes(entries, ctx.pre.get("device_time", ""), dev.run_lines())
        return v, files, None
    _record(chk, attempt)


GATES = {
    "G1": gate_g1, "G1L": gate_g1_launcher3, "G2": gate_g2, "G4": gate_g4, "G5": gate_g5, "G6": gate_g6,
    "G3": gate_g3, "G7": gate_g7, "PO": gate_panel_off, "G9": gate_g9,
}
DEFAULT = ["G1", "G2", "G4", "G5", "G6", "G9"]
ORDER = ["G1", "G1L", "G2", "G4", "G5", "G6", "G3", "G7", "PO", "G9"]
