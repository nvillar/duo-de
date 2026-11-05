"""Regressions from the first hardware run (duogate-20261104-224305-2036, image m22recents).

Each case reproduces what the run showed, with the real dumps where they matter, and asserts the corrected verdict."""
import shutil
import tempfile
import unittest

from duogate import checks as C
from duogate import gates, preconditions
from duogate.device import Device
from duogate.logstream import KEEP_TAGS, LogBuffer
from duogate.model import FAIL, INCOMPLETE, PASS, EvidenceError, Rect
from duogate.parsers import parse_log_line, minimize_event, policy_minimized_panel
from duogate.prompts import Operator

from tests import sim
from tests.sim import FakeAdb, Sim, Snap, Task, snap_for


def line(msg, tag="DuoDisplayAreaPolicy"):
    return parse_log_line("11-05 06:48:25.000  1000  1200 I %s: %s" % (tag, msg))


class M22MinimizeReasons(unittest.TestCase):
    """G2.crossHome: M2.2 (fw 0018) logs the gesture Home as 'Home from Recents' / 'Recents #n', not 'Home'."""

    def test_all_reasons_parse(self):
        for msg, want in (("Home: minimizing Panel1 (1 tasks)", ("Home", "Panel1", 1)),
                          ("Home from Recents: minimizing Panel0 (1 tasks)", ("Home from Recents", "Panel0", 1)),
                          ("Recents #1: minimizing Panel0 (2 tasks)", ("Recents #1", "Panel0", 2))):
            self.assertEqual(minimize_event(line(msg)), want)
            self.assertEqual(policy_minimized_panel(line(msg)), want[1:])
        self.assertIsNone(minimize_event(line("Restored minimized Task{1 #21 x}")))
        self.assertIsNone(minimize_event(line("Home from Recents: minimizing Panel0 (1 tasks)", tag="Other")))
        self.assertIn("DuoRecents", KEEP_TAGS)

    def test_gesture_home_on_the_gestured_panel_passes(self):
        a, b = Task(47, "ProbeTestActivity", "A"), Task(48, "ProbeOtherTestActivity", "B")
        a.hidden = 8
        buf = LogBuffer()
        for raw in (sim.marker("r.1"), sim.line("DuoDisplayAreaPolicy", "Home from Recents: minimizing Panel0 (1 tasks)",
                                                pid=1000), sim.probe("onStop", a)):
            buf.feed(raw)
        v = C.eval_home(buf.between(0), buf.events(0), snap_for([a, b]), "A", 47, 48)
        self.assertEqual(v.status, PASS, v.reasons)
        self.assertTrue(any("Home from Recents" in n for n in v.notes))
        v = C.eval_home(buf.between(0), buf.events(0), snap_for([a, b]), "B", 48, 47)
        self.assertEqual(v.status, FAIL)


class M22StuckBack(unittest.TestCase):
    """G4: after a synthetic Back swipe on A the preview never finished: task 50's input frame stayed at the 0.8
    preview scale and task 51 (panel B) was dropped from input, so taps on B reached the spanning probe HOME."""

    def snap(self, which):
        return Snap(windows=sim.fixture("g4-%s-windows.txt" % which), input=sim.fixture("g4-%s-input.txt" % which))

    def test_real_dumps(self):
        self.assertEqual(C.eval_input_ok(self.snap("before"), 50, "A").status, PASS)
        self.assertEqual(C.eval_input_ok(self.snap("before"), 51, "B").status, PASS)
        a = C.eval_input_ok(self.snap("stuck"), 50, "A")
        self.assertEqual(a.status, FAIL)
        self.assertTrue(any("[135,180][1215,1620]" in r for r in a.reasons), a.reasons)
        b = C.eval_input_ok(self.snap("stuck"), 51, "B")
        self.assertEqual(b.status, FAIL)
        self.assertTrue(any("not touchable" in r for r in b.reasons), b.reasons)

    def tmpctx(self, bugs):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        fake = FakeAdb()
        dev = Device(fake, tmp, "t", settle=0.05, quiet=0, screens="none", live=False)
        dev.remove_wait = 0
        fake.sim = Sim(dev.log, bugs)
        return gates.Ctx(dev, Operator(interactive=False), 3, {"navigation_mode": "2"}), fake

    def test_first_attempt_fails_with_the_cause_and_later_attempts_start_clean(self):
        ctx, fake = self.tmpctx({"stuck_back"})
        gates.gate_g4(ctx)
        back = next(c for c in ctx.checks if c.id == "G4.back.A")
        self.assertEqual(back.status, FAIL)
        first = " ".join(back.attempts[0].reasons)
        self.assertIn("lost input", first)
        self.assertIn("transformed", first)
        self.assertTrue(any("pair re-created" in r for r in back.attempts[0].reasons), back.attempts[0].reasons)
        for a in back.attempts[1:]:
            # each later repetition ran on a fresh pair: no inherited 'touch reached the probe HOME'
            self.assertFalse(any("ProbeHomeActivity" in r for r in a.reasons), a.reasons)
            self.assertTrue(any("lost input" in r for r in a.reasons), a.reasons)

    def test_without_recreation_the_inherited_state_would_misattribute(self):
        # the sim reproduces the hardware routing: a tap on B goes to the probe HOME once B lost input
        ctx, fake = self.tmpctx({"stuck_back"})
        pair = gates.setup_pair(ctx, gates.PAIR_KEYS, "s")
        fake.sim.back("A")
        v, _, _ = gates.focus_by_touch(ctx, "B", pair["B"], (pair["A"],), "f")
        self.assertEqual(v.status, FAIL)
        self.assertTrue(any("ProbeHomeActivity" in r for r in v.reasons), v.reasons)


class M22TapTargets(unittest.TestCase):
    """G2.crossHome.B: the synthetic Home swipe scrolled probe A (title at [40,-30]); G5.ime.A.editor2: editor 2 at
    y=1020 was under the keyboard (ime bottom 1008, keyboard top 792), so the tap reached the IME."""

    def dev(self, bugs=()):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        fake = FakeAdb()
        dev = Device(fake, tmp, "t", settle=0.05, quiet=0, screens="none", live=False)
        fake.sim = Sim(dev.log, bugs)
        t = Task(50, "ProbeTestActivity", "A")
        fake.sim.tasks.append(t)
        return dev, fake, t

    def test_scrolled_title_is_scrolled_back_with_gate3(self):
        dev, fake, t = self.dev({"scroll_targets"})
        t.scroll = 290
        fake.sim.emit(sim.targets_for(t))
        x, y = dev.task_point(50, "title", "A")
        self.assertTrue(220 < y < 1720, y)
        self.assertEqual(t.scroll, 0)
        self.assertTrue(any(c.startswith("input swipe") for c in fake.sim.commands))

    def test_editor_under_the_keyboard_is_scrolled_into_view_with_gate3(self):
        dev, fake, t = self.dev({"scroll_targets"})
        fake.sim.emit(sim.targets_for(t))
        fake.sim.emit(sim.probe("insets", t, "imeVisible=true ime=[0,0,0,1008] bars=[0,220,0,1008] cutout=null"))
        x, y = dev.task_point(50, "editor2", "A")
        self.assertTrue(220 + 10 <= y <= 1800 - 1008 - 10, y)      # above the keyboard top (792)
        self.assertGreater(t.scroll, 0)

    def test_visible_target_needs_no_scroll(self):
        dev, fake, t = self.dev({"scroll_targets"})
        fake.sim.emit(sim.targets_for(t))
        fake.sim.emit(sim.probe("insets", t, "imeVisible=true ime=[0,0,0,1008] bars=[0,220,0,1008] cutout=null"))
        self.assertEqual(dev.task_point(50, "title", "A"), (675, 300))
        self.assertFalse(any(c.startswith("input swipe") for c in fake.sim.commands))

    def test_gate2_probe_cannot_report_the_new_position(self):
        dev, fake, t = self.dev()
        fake.sim.emit(sim.targets_for(t))
        fake.sim.emit(sim.probe("insets", t, "imeVisible=true ime=[0,0,0,1008] bars=[0,220,0,1008] cutout=null"))
        with self.assertRaises(EvidenceError) as cm:
            dev.task_point(50, "editor2", "A")
        self.assertIn("gate-3", str(cm.exception))

    def test_probe_protocols(self):
        self.assertEqual(preconditions.PROBE_PROTOCOLS, ("gate-2", "gate-3"))


class M22Rotate(unittest.TestCase):
    """G5.rotate: the editor tap was received and focused it, but no keyboard came: FAIL, as in eval_ime."""

    def test_no_keyboard_after_the_tap_fails(self):
        a, b = Task(52, "ProbeTestActivity", "A"), Task(53, "ProbeOtherTestActivity", "B")
        buf = LogBuffer()
        for raw in (sim.marker("r.1"), sim.probe("editorFocus", a, "editor=1 focus=true")):
            buf.feed(raw)
        v = C.eval_ime_rotated(buf.events(0), snap_for([a, b], ime_area="A"), 52, 53)
        self.assertEqual(v.status, FAIL, v.reasons)


class M22PanelOff(unittest.TestCase):
    """PO: the keyguard came back after the reboot (no secure lock) and the gate died with 'device still locked'."""

    def ctx(self, credential, bugs=()):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        fake = FakeAdb()
        dev = Device(fake, tmp, "t", settle=0.05, quiet=0, screens="none", live=False)
        dev.remove_wait = 0
        dev.boot_wait = 0
        fake.sim = Sim(dev.log, bugs)
        fake.sim.credential = credential
        pre = {"secure_lock": credential, "panels": {"persist.sys.phh.duo.panels": "",
                                                     "persist.debug.phh.duo.panels": "1"}}
        ctx = gates.Ctx(dev, Operator(interactive=False), 1, pre, {"allow_reboot": True})
        return ctx, fake

    def test_non_secure_keyguard_is_dismissed_after_each_reboot(self):
        ctx, fake = self.ctx("NONE")
        gates.gate_panel_off(ctx)
        s = {c.id: c.status for c in ctx.checks}
        self.assertEqual(fake.sim.reboots, 2)
        self.assertEqual(s, {"PO.panelOff": PASS, "PO.launch.A": PASS, "PO.launch.B": PASS, "PO.restored": PASS},
                         [c.to_json() for c in ctx.checks])
        self.assertEqual(fake.sim.commands.count("wm dismiss-keyguard"), 2)
        self.assertEqual(len(ctx.pre["keyguard_after_reboot"]), 2)
        self.assertEqual(fake.sim.props["persist.debug.phh.duo.panels"], "1")

    def test_secure_keyguard_without_operator_is_incomplete_not_a_tool_error(self):
        ctx, fake = self.ctx("PIN")
        gates.gate_panel_off(ctx)                 # must not raise
        s = {c.id: c.status for c in ctx.checks}
        self.assertTrue(all(v == INCOMPLETE for v in s.values()), s)
        self.assertNotIn("wm dismiss-keyguard", fake.sim.commands)
        self.assertEqual(fake.sim.reboots, 2)                          # the properties are still restored + applied
        self.assertEqual(fake.sim.props["persist.debug.phh.duo.panels"], "1")
        self.assertTrue(all("after reboot" in (c.skipped or "") for c in ctx.checks), [c.skipped for c in ctx.checks])



class HomeTargetsWithoutLog(unittest.TestCase):
    """Second hardware run (duogate-20261104-231722-b139): the probe home had logged its targets before the log
    buffer window, so the tool fell back to uiautomator. That works right after Home, but once a probe task on the
    other panel is focused uiautomator dumps only that window: 'no probe home buttons'. Positions found once are now
    kept until the home lays out again."""

    def ctx(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        fake = FakeAdb()
        dev = Device(fake, tmp, "t", settle=0.05, quiet=0, screens="none", live=False)
        dev.remove_wait = 0
        fake.sim = Sim(dev.log, {"focused_dump"})
        fake.sim.ui_xml = sim.fixture("probe-home-gate2-uiautomator.xml")
        dev.log.lines.clear()                      # the home's targets event rolled out of the log buffer
        return gates.Ctx(dev, Operator(interactive=False), 1, {"navigation_mode": "2"}), fake, tmp

    def test_pair_setup_uses_the_positions_found_while_the_home_was_focused(self):
        ctx, fake, _ = self.ctx()
        pair = gates.setup_pair(ctx, gates.PAIR_KEYS, "G4.setup")
        self.assertNotEqual(pair["A"], pair["B"])
        self.assertEqual(fake.sim.ui_dumps, 1)       # one dump, while the home was focused

    def test_relayout_of_the_home_invalidates_the_cache(self):
        ctx, fake, tmp = self.ctx()
        dev = ctx.dev
        self.assertIn("A.probeTest", dev.home_targets())
        gates.launch_from_home(ctx, "A", "probeTest", "launch")        # focus moves to the probe test on A
        fake.sim.emit(sim.probe("onConfigurationChanged", sim.HOME_TASK, "display=0 rotation=0",
                                act="ProbeHomeActivity@77"))
        with self.assertRaises(EvidenceError) as cm:
            dev.home_targets()
        self.assertIn("focused window", str(cm.exception))
        self.assertEqual(fake.sim.ui_dumps, 2)
        import os
        extra = sorted(os.listdir(os.path.join(tmp, "extra")))
        self.assertEqual(extra, ["home-targets-uiautomator-1.txt", "home-targets-uiautomator-2.txt"])
        with open(os.path.join(tmp, "extra", extra[1]), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("org.duode.probe", text)
        self.assertNotIn("Editor", text)             # no on-screen text is stored

    def test_a_newer_targets_event_wins(self):
        ctx, fake, _ = self.ctx()
        dev = ctx.dev
        dev.home_targets()
        fake.sim.emit(sim.probe("targets", sim.HOME_TASK, "A.probeTest=[40,500][1310,660]",
                                act="ProbeHomeActivity@77"))
        self.assertEqual(dev.home_targets()["A.probeTest"], Rect(40, 500, 1310, 660))
        self.assertEqual(fake.sim.ui_dumps, 1)


class AstraReviewF261(unittest.TestCase):
    """Astra (high) on 4e80dbe + f261c9d."""

    def tmpctx(self, bugs=(), reps=2):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        fake = FakeAdb()
        dev = Device(fake, tmp, "t", settle=0.05, quiet=0, screens="none", live=False)
        dev.remove_wait = 0
        fake.sim = Sim(dev.log, set(bugs))
        return gates.Ctx(dev, Operator(interactive=False), reps, {"navigation_mode": "2"}), fake

    # H1 -----------------------------------------------------------------------------------------------------------
    def test_reused_tasks_without_input_fail_g2(self):
        ctx, fake = self.tmpctx({"reuse_loses_input"})
        gates.gate_g2(ctx)
        s = {c.id: c for c in ctx.checks}
        self.assertEqual(s["G2.restore.A"].status, FAIL, s["G2.restore.A"].to_json())
        self.assertEqual(s["G2.restore.B"].status, FAIL)
        self.assertTrue(any("empty touchable region" in r for a in s["G2.restore.A"].attempts for r in a.reasons))
        from duogate import report
        g = report.gate_verdicts([c.to_json() for c in ctx.checks], ["G2"], {})
        self.assertEqual(g["G2"]["status"], FAIL)

    def test_broken_pair_found_at_clean_up_fails_the_attempt(self):
        ctx, fake = self.tmpctx()
        pair = gates.setup_pair(ctx, gates.PAIR_KEYS, "s")
        check = ctx.check("X", "G", "t", required=1)
        from duogate.model import Verdict
        check.add(Verdict())                                  # the attempt's own assertions passed
        next(t for t in fake.sim.tasks if t.tid == pair["B"]).empty_region = True
        old = dict(pair)
        gates.restore_pair(ctx, pair, gates.PAIR_KEYS, "clean", check)
        self.assertEqual(check.status, FAIL)
        self.assertTrue(any("not reachable by input" in r for r in check.attempts[0].reasons))
        self.assertNotEqual(pair, old)                        # re-created for the next repetition

    def test_broken_pair_without_a_check_is_not_silent(self):
        ctx, fake = self.tmpctx()
        pair = gates.setup_pair(ctx, gates.PAIR_KEYS, "s")
        next(t for t in fake.sim.tasks if t.tid == pair["A"]).empty_region = True
        with self.assertRaises(gates.SetupError):
            gates.restore_pair(ctx, pair, gates.PAIR_KEYS, "clean")

    # H2 -----------------------------------------------------------------------------------------------------------
    def region_snap(self, region):
        text = sim.fixture("g4-before-input.txt")
        old = "touchableRegion=[1434,0][2784,1800]"
        i = text.index("name=960d37a ")
        j = text.index(old, i)
        return Snap(windows=sim.fixture("g4-before-windows.txt"), input=text[:j] + "touchableRegion=" + region
                    + text[j + len(old):])

    def test_empty_region_with_normal_flags_fails(self):
        self.assertEqual(C.eval_input_ok(self.region_snap("[1434,0][2784,1800]"), 51, "B").status, PASS)
        v = C.eval_input_ok(self.region_snap("<empty>"), 51, "B")
        self.assertEqual(v.status, FAIL)
        self.assertEqual(C.eval_input_ok(self.region_snap("[1434,0][2000,900]"), 51, "B").status, FAIL)
        self.assertEqual(C.eval_input_ok(self.region_snap("SkRegion((1434,0,2784,1800))"), 51, "B").status,
                         INCOMPLETE)

    def test_eval_back_with_the_other_region_empty_fails(self):
        a, b = Task(50, "ProbeTestActivity", "A"), Task(51, "ProbeOtherTestActivity", "B")
        a.stopped, b.empty_region = True, True
        buf = LogBuffer()
        for raw in (sim.marker("r.1"), sim.probe("onPause", a), sim.probe("onStop", a)):
            buf.feed(raw)
        self.assertEqual(C.eval_back(buf.events(0), snap_for([a, b]), "A", 50, 51).status, FAIL)

    # M1 -----------------------------------------------------------------------------------------------------------
    def test_stale_bounds_on_the_other_panel_send_no_input(self):
        ctx, fake = self.tmpctx({"scroll_targets"})
        t = Task(50, "ProbeTestActivity", "A")
        fake.sim.tasks.append(t)
        fake.sim.emit(sim.probe("targets", t, "title=[2000,260][2600,341]"))
        with self.assertRaises(EvidenceError) as cm:
            ctx.dev.task_point(50, "title", "A")
        self.assertIn("not on the requested panel", str(cm.exception))
        self.assertFalse(any(c.startswith("input ") for c in fake.sim.commands), fake.sim.commands)

    def test_drag_endpoints_stay_in_the_panel_content(self):
        ctx, fake = self.tmpctx({"scroll_targets"})
        t = Task(50, "ProbeTestActivity", "A")
        fake.sim.tasks.append(t)
        fake.sim.emit(sim.targets_for(t))
        fake.sim.emit(sim.probe("insets", t, "imeVisible=true ime=[0,0,0,1008] bars=[0,220,0,1008] cutout=null"))
        ctx.dev.task_point(50, "editor2", "A")
        import re
        for c in fake.sim.commands:
            m = re.match(r"input swipe (\d+) (\d+) (\d+) (\d+)", c)
            if m:
                x0, y0, x1, y1 = (int(g) for g in m.groups())
                self.assertTrue(0 < x0 < 1350 and 0 < x1 < 1350, c)
                self.assertTrue(220 <= y0 <= 792 and 220 <= y1 <= 792, c)


if __name__ == "__main__":
    unittest.main()

class AstraReview69fc(unittest.TestCase):
    """Astra (high) on 69fc154."""

    def ctx(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        fake = FakeAdb()
        dev = Device(fake, tmp, "t", settle=0.05, quiet=0, screens="none", live=False)
        dev.remove_wait = 0
        fake.sim = Sim(dev.log, {"focused_dump"})
        fake.sim.ui_xml = sim.fixture("probe-home-gate2-uiautomator.xml")
        return gates.Ctx(dev, Operator(interactive=False), 1, {"navigation_mode": "2"}), fake

    def home_event(self, fake, event, rest=""):
        fake.sim.emit(sim.probe(event, sim.HOME_TASK, rest, act="ProbeHomeActivity@77"))

    # 1: log-backed positions are invalidated like cached ones --------------------------------------------------------
    def test_relayout_after_the_targets_event_invalidates_it(self):
        for event in ("onConfigurationChanged", "layout", "onDestroy", "onCreate"):
            ctx, fake = self.ctx()
            dev = ctx.dev
            self.assertIn("A.probeTest", dev.home_targets())          # from the Sim's targets event, no dump
            self.assertEqual(fake.sim.ui_dumps, 0)
            gates.launch_from_home(ctx, "A", "probeTest", "launch")   # a probe task now has focus
            self.home_event(fake, event, "rootOnScreen=[0,0][2784,1800]")
            with self.assertRaises(EvidenceError, msg=event):
                dev.home_targets()                                   # stale positions are not used
            self.assertEqual(fake.sim.ui_dumps, 1, event)

    def test_targets_logged_after_the_relayout_are_current(self):
        ctx, fake = self.ctx()
        self.home_event(fake, "layout", "rootOnScreen=[0,0][2784,1800]")
        fake.sim.emit(sim.home_targets())
        self.assertIn("B.probeTest2", ctx.dev.home_targets())
        self.assertEqual(fake.sim.ui_dumps, 0)

    # 2: clean-up merges statuses ------------------------------------------------------------------------------------
    def broken_cleanup(self, region, earlier=None):
        from duogate.model import Verdict
        ctx, fake = self.ctx()
        fake.sim.bugs.discard("focused_dump")
        pair = gates.setup_pair(ctx, gates.PAIR_KEYS, "s")
        check = ctx.check("X", "G", "t", required=1)
        check.add(earlier or Verdict())
        task = next(t for t in fake.sim.tasks if t.tid == pair["B"])
        if region == "<empty>":
            task.empty_region = True
        else:
            task.region_text = region
        gates.restore_pair(ctx, pair, gates.PAIR_KEYS, "clean", check)
        return check

    def test_unknown_region_format_is_incomplete_not_fail(self):
        check = self.broken_cleanup("SkRegion((1434,0,2784,1800))")
        self.assertEqual(check.status, INCOMPLETE, check.to_json())
        self.assertTrue(any("could not be confirmed" in r for r in check.attempts[0].reasons))

    def test_definite_defect_is_fail(self):
        self.assertEqual(self.broken_cleanup("<empty>").status, FAIL)

    def test_unknown_evidence_never_downgrades_an_earlier_fail(self):
        from duogate.model import Verdict
        check = self.broken_cleanup("SkRegion((1434,0,2784,1800))", Verdict().fail("own assertion"))
        self.assertEqual(check.status, FAIL)


if __name__ == "__main__":
    unittest.main()