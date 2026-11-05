"""Test the tester: known-good evidence passes; wrong panel, stale evidence, missing events, ambiguous identity and
parser mismatches never pass. Expected geometry in these tests is literal (not computed by the code under test)."""
import unittest

from duogate import checks as C
from duogate.logstream import LogBuffer
from duogate.model import FAIL, INCOMPLETE, PASS, Check, Rect, Verdict
from duogate.parsers import parse_logcat

from tests import sim
from tests.sim import Snap, Task, snap_for

P0, P1, DISPLAY = Rect(0, 0, 1350, 1800), Rect(1434, 0, 2784, 1800), Rect(0, 0, 2784, 1800)


def buffer(*raw):
    b = LogBuffer()
    for r in raw:
        b.feed(r)
    return b


def window(*raw, mid="r.1"):
    """Events and lines after a marker, as a gate step sees them."""
    b = buffer(*raw)
    start = b.find_marker(mid)
    assert start is not None
    return b.events(start), b.between(start)


class Geometry(unittest.TestCase):
    def test_duo1_constants_are_literal(self):
        self.assertEqual(C.DUO1.panel("A"), P0)
        self.assertEqual(C.DUO1.panel("B"), P1)
        self.assertEqual(C.DUO1.display, DISPLAY)

    def test_real_panel_capture_matches(self):
        self.assertEqual(C.check_geometry(Snap(containers=sim.fixture("panel-containers.txt"))).status, PASS)

    def test_wrong_panel_bounds_fail(self):
        text = sim.fixture("panel-containers.txt").replace(
            "Panel1Tasks type=undefined mode=fullscreen override-mode=undefined requested-bounds=[0,0][0,0] "
            "bounds=[1434,0][2784,1800]",
            "Panel1Tasks type=undefined mode=fullscreen override-mode=undefined requested-bounds=[0,0][0,0] "
            "bounds=[1392,0][2784,1800]")
        self.assertEqual(C.check_geometry(Snap(containers=text)).status, FAIL)

    def test_panel_off_capture_is_incomplete(self):
        self.assertEqual(C.check_geometry(Snap(containers=sim.fixture("off-containers.txt"))).status, INCOMPLETE)


class Launch(unittest.TestCase):
    def setUp(self):
        self.a = Task(20, "ProbeTestActivity", "A")
        self.b = Task(21, "ProbeOtherTestActivity", "B")

    def run_launch(self, raw, tasks, side="A", **kw):
        events, _ = window(*raw)
        return C.eval_launch(events, snap_for(tasks), side, label="Probe test", act_class="ProbeTestActivity", **kw)

    def test_good_launch_passes(self):
        v, tid = self.run_launch([sim.marker("r.1"), sim.launch("A", "probeTest"), sim.resume(self.a)],
                                 [self.a, self.b])
        self.assertEqual((v.status, tid), (PASS, 20), v.reasons)

    def test_wrong_panel_fails(self):
        wrong = Task(20, "ProbeTestActivity", "B")
        v, _ = self.run_launch([sim.marker("r.1"), sim.launch("A", "probeTest"), sim.resume(wrong)], [wrong, self.b])
        self.assertEqual(v.status, FAIL)
        self.assertTrue(any("Panel1Tasks, expected Panel0Tasks" in r for r in v.reasons), v.reasons)
        self.assertTrue(any("window bounds" in r for r in v.reasons), v.reasons)

    def test_wrong_bounds_in_app_only_fails(self):
        v, _ = self.run_launch([sim.marker("r.1"), sim.launch("A", "probeTest"), sim.resume(self.a, DISPLAY)],
                               [self.a, self.b])
        self.assertEqual(v.status, FAIL)

    def test_stale_evidence_before_the_marker_is_ignored(self):
        v, tid = self.run_launch([sim.launch("A", "probeTest"), sim.resume(self.a), sim.marker("r.1")],
                                 [self.a, self.b])
        self.assertEqual((v.status, tid), (INCOMPLETE, None))

    def test_missing_resume_is_incomplete(self):
        v, tid = self.run_launch([sim.marker("r.1"), sim.launch("A", "probeTest")], [self.a, self.b])
        self.assertEqual((v.status, tid), (INCOMPLETE, None))

    def test_ambiguous_identity_is_incomplete(self):
        other = Task(25, "ProbeTestActivity", "A")
        v, tid = self.run_launch([sim.marker("r.1"), sim.launch("A", "probeTest"), sim.resume(self.a),
                                  sim.resume(other)], [self.a, other, self.b])
        self.assertEqual((v.status, tid), (INCOMPLETE, None))

    def test_tap_on_the_wrong_button_is_incomplete_not_fail(self):
        v, _ = self.run_launch([sim.marker("r.1"), sim.launch("B", "probeTest"), sim.resume(self.a)],
                               [self.a, self.b])
        self.assertEqual(v.status, INCOMPLETE)

    def test_reuse_that_creates_a_new_task_fails(self):
        v, _ = self.run_launch([sim.marker("r.1"), sim.launch("A", "probeTest"), sim.resume(self.a)],
                               [self.a, self.b], expect_task=17)
        self.assertEqual(v.status, FAIL)

    def test_task_missing_from_dumps_is_incomplete(self):
        v, _ = self.run_launch([sim.marker("r.1"), sim.launch("A", "probeTest"), sim.resume(self.a)], [self.b])
        self.assertEqual(v.status, INCOMPLETE)

    def test_unparsable_dump_is_incomplete(self):
        events, _ = window(sim.marker("r.1"), sim.launch("A", "probeTest"), sim.resume(self.a))
        snap = Snap(containers="Error: no such service\n", activities=sim.activities([self.a]))
        v, _ = C.eval_launch(events, snap, "A", label="Probe test", act_class="ProbeTestActivity")
        self.assertEqual(v.status, INCOMPLETE)

    def test_still_policy_hidden_after_relaunch_fails(self):
        self.a.hidden = 8
        events, _ = window(sim.marker("r.1"), sim.launch("A", "probeTest"), sim.resume(self.a))
        snap = Snap(containers=sim.containers([self.a, self.b]), activities=sim.activities([self.a, self.b]))
        v, _ = C.eval_launch(events, snap, "A", label="Probe test", act_class="ProbeTestActivity")
        self.assertEqual(v.status, FAIL)

    def test_default_area_launch(self):
        d = Task(30, "ProbeTestActivity", "default")
        events, _ = window(sim.marker("r.1"), sim.launch("A", "probeTest"), sim.resume(d))
        v, _ = C.eval_launch(events, snap_for([d], panels=False), "A", label="Probe test",
                             act_class="ProbeTestActivity", area="default")
        self.assertEqual(v.status, PASS, v.reasons)
        # the same task in a panel is a failure of the panel-off control
        v = C.check_default_area(snap_for([self.a]), 20)
        self.assertEqual(v.status, FAIL)


class Input(unittest.TestCase):
    def setUp(self):
        self.a = Task(20, "ProbeTestActivity", "A")
        self.b = Task(21, "ProbeOtherTestActivity", "B")

    def touch(self, task, x=700, y=300):
        return sim.probe("touch", task, "action=down x=1 y=1 rawX=%d rawY=%d pointers=1" % (x, y))

    def test_touch_receipt(self):
        events, _ = window(sim.marker("r.1"), self.touch(self.a))
        self.assertEqual(C.eval_touch(events, 700, 300, 20, (21,)).status, PASS)

    def test_touch_on_the_other_task_fails(self):
        events, _ = window(sim.marker("r.1"), self.touch(self.b))
        self.assertEqual(C.eval_touch(events, 700, 300, 20, (21,)).status, FAIL)

    def test_touch_on_home_fails(self):
        events, _ = window(sim.marker("r.1"), sim.probe("touch", 19, "action=down rawX=700 rawY=300",
                                                       act="ProbeHomeActivity@77"))
        self.assertEqual(C.eval_touch(events, 700, 300, 20, (21,)).status, FAIL)

    def test_touch_not_seen_is_incomplete(self):
        events, _ = window(self.touch(self.a), sim.marker("r.1"))
        self.assertEqual(C.eval_touch(events, 700, 300, 20, (21,)).status, INCOMPLETE)

    def test_text_sentinel(self):
        events, _ = window(sim.marker("r.1"), sim.probe("text", self.a, "editor=1 len=11 sentinels=dgs0123abcd"))
        self.assertEqual(C.eval_text(events, "dgs0123abcd", 20, (21,)).status, PASS)
        events, _ = window(sim.marker("r.1"), sim.probe("text", self.b, "editor=1 len=11 sentinels=dgs0123abcd"))
        self.assertEqual(C.eval_text(events, "dgs0123abcd", 20, (21,)).status, FAIL)
        events, _ = window(sim.probe("text", self.a, "editor=1 len=11 sentinels=dgs0123abcd"), sim.marker("r.1"))
        self.assertEqual(C.eval_text(events, "dgs0123abcd", 20, (21,)).status, INCOMPLETE)
        events, _ = window(sim.marker("r.1"), sim.probe("text", self.a, "editor=1 len=22 sentinels=dgs0123abcd"))
        self.assertEqual(C.eval_text(events, "dgs99999999", 20, (21,)).status, INCOMPLETE)

    def test_focus(self):
        tasks = [self.a, self.b]
        self.assertEqual(C.eval_focus(snap_for(tasks, focus=self.a), 20).status, PASS)
        self.assertEqual(C.eval_focus(snap_for(tasks, focus=self.b), 20).status, FAIL)
        snap = sim.Snap(containers=sim.containers(tasks), activities=sim.activities(tasks),
                        windows=sim.windows(tasks), wdisplays=sim.wdisplays(self.a), input=sim.inputdump(tasks, self.b))
        v = C.eval_focus(snap, 20)
        self.assertEqual(v.status, FAIL)
        self.assertTrue(any("input focus" in r for r in v.reasons), v.reasons)


class HomeBack(unittest.TestCase):
    def setUp(self):
        self.a = Task(20, "ProbeTestActivity", "A")
        self.b = Task(21, "ProbeOtherTestActivity", "B")

    def home_line(self, panel):
        return sim.line("DuoDisplayAreaPolicy", "Home: minimizing %s (1 tasks)" % panel, pid=1000)

    def test_home_on_b(self):
        self.b.hidden = 8
        events, lines = window(sim.marker("r.1"), self.home_line("Panel1"), sim.probe("onStop", self.b))
        v = C.eval_home(lines, events, snap_for([self.a, self.b]), "B", 21, 20)
        self.assertEqual(v.status, PASS, v.reasons)

    def test_home_minimizes_the_focused_instead_of_the_gestured_panel(self):
        self.a.hidden = 8
        events, lines = window(sim.marker("r.1"), self.home_line("Panel0"), sim.probe("onStop", self.a))
        v = C.eval_home(lines, events, snap_for([self.a, self.b]), "B", 21, 20)
        self.assertEqual(v.status, FAIL)

    def test_home_not_delivered_is_incomplete(self):
        events, lines = window(sim.marker("r.1"))
        self.assertEqual(C.eval_home(lines, events, snap_for([self.a, self.b]), "B", 21, 20).status, INCOMPLETE)

    def test_home_that_stops_the_other_panel_fails(self):
        self.b.hidden = 8
        events, lines = window(sim.marker("r.1"), self.home_line("Panel1"), sim.probe("onStop", self.a))
        self.assertEqual(C.eval_home(lines, events, snap_for([self.a, self.b]), "B", 21, 20).status, FAIL)

    def test_back(self):
        self.a.stopped = True
        events, _ = window(sim.marker("r.1"), sim.probe("onPause", self.a), sim.probe("onStop", self.a))
        self.assertEqual(C.eval_back(events, snap_for([self.a, self.b]), "A", 20, 21).status, PASS)

    def test_back_on_the_focused_panel_fails(self):
        self.b.stopped = True
        events, _ = window(sim.marker("r.1"), sim.probe("onPause", self.b), sim.probe("onStop", self.b))
        self.assertEqual(C.eval_back(events, snap_for([self.a, self.b]), "A", 20, 21).status, FAIL)

    def test_back_not_recognized_is_incomplete(self):
        events, _ = window(sim.marker("r.1"))
        self.assertEqual(C.eval_back(events, snap_for([self.a, self.b]), "A", 20, 21).status, INCOMPLETE)

    def test_restored(self):
        _, lines = window(sim.marker("r.1"), sim.line("DuoDisplayAreaPolicy", "Restored minimized Task{1 #21 x}"))
        self.assertEqual(C.eval_restored(lines, 21).status, PASS)
        self.assertEqual(C.eval_restored(lines, 20).status, INCOMPLETE)


class Ime(unittest.TestCase):
    def setUp(self):
        self.a = Task(20, "ProbeTestActivity", "A")
        self.b = Task(21, "ProbeOtherTestActivity", "B")
        b = buffer(sim.probe("editorFocus", self.a, "editor=1 focus=true"),
                   sim.probe("insets", self.a, "imeVisible=true ime=[0,0,0,1008] bars=[0,60,0,80] cutout=null"),
                   sim.probe("insets", self.b, "imeVisible=true ime=[0,0,0,0] bars=[0,60,0,80] cutout=null"),
                   sim.probe("insets", self.b, "imeVisible=false ime=[0,0,0,1008] bars=[0,60,0,80] cutout=null"))
        self.ev = [b.events(-1)[i] for i in range(4)]

    def ime(self, snap, focus=0, t_ins=1, o_ins=2):
        return C.eval_ime(snap, "A", 1, 20, 21, target_focus=self.ev[focus] if focus is not None else None,
                          target_insets=self.ev[t_ins], other_insets=self.ev[o_ins] if o_ins is not None else None)

    def test_keyboard_on_the_target_panel(self):
        v = self.ime(snap_for([self.a, self.b], focus=self.a, ime=self.a, ime_area="A"))
        self.assertEqual(v.status, PASS, v.reasons)

    def test_ime_visible_with_zero_insets_on_the_other_task_is_not_a_failure(self):
        # F10 correction: WindowInsets.isVisible(ime) may be true for non-target windows with zero insets
        v = self.ime(snap_for([self.a, self.b], focus=self.a, ime=self.a, ime_area="A"), o_ins=2)
        self.assertEqual(v.status, PASS, v.reasons)

    def test_other_task_with_ime_insets_fails(self):
        v = self.ime(snap_for([self.a, self.b], focus=self.a, ime=self.a, ime_area="A"), o_ins=3)
        self.assertEqual(v.status, FAIL)

    def test_keyboard_on_the_wrong_panel_fails(self):
        v = self.ime(snap_for([self.a, self.b], focus=self.a, ime=self.a, ime_area="B"))
        self.assertEqual(v.status, FAIL)

    def test_ime_target_is_the_other_task_fails(self):
        v = self.ime(snap_for([self.a, self.b], focus=self.a, ime=self.b, ime_area="A"))
        self.assertEqual(v.status, FAIL)

    def test_editor_without_focus_fails(self):
        v = self.ime(snap_for([self.a, self.b], focus=self.a, ime=self.a, ime_area="A"), focus=None)
        self.assertEqual(v.status, FAIL)

    def test_no_insets_from_the_other_task_is_incomplete(self):
        v = self.ime(snap_for([self.a, self.b], focus=self.a, ime=self.a, ime_area="A"), o_ins=None)
        self.assertEqual(v.status, INCOMPLETE)


class Keyguard(unittest.TestCase):
    def setUp(self):
        self.a = Task(20, "ProbeLockedTestActivity", "A")
        self.b = Task(21, "ProbeOtherTestActivity", "B")

    def test_locked_and_safe(self):
        events, _ = window(sim.marker("r.1"))
        v = C.eval_locked(events, snap_for([self.a, self.b], keyguard=True), (21,), 20)
        self.assertEqual(v.status, PASS, v.reasons)

    def test_ordinary_window_on_screen_is_a_leak(self):
        events, _ = window(sim.marker("r.1"))
        v = C.eval_locked(events, snap_for([self.a, self.b], keyguard=True, leak=21), (21,), 20)
        self.assertEqual(v.status, FAIL)

    def test_ordinary_window_touchable_is_a_leak(self):
        events, _ = window(sim.marker("r.1"))
        v = C.eval_locked(events, snap_for([self.a, self.b], keyguard=True, touchable_leak=21), (21,), 20)
        self.assertEqual(v.status, FAIL)

    def test_attempted_tap_delivered_is_a_leak(self):
        events, _ = window(sim.marker("r.1"), sim.probe("touch", self.b, "action=down rawX=2000 rawY=300"))
        v = C.eval_locked(events, snap_for([self.a, self.b], keyguard=True), (21,), 20)
        self.assertEqual(v.status, FAIL)

    def test_not_locked_is_incomplete(self):
        events, _ = window(sim.marker("r.1"))
        snap = Snap(activities=sim.activities([self.a, self.b], keyguard=False),
                    windows=sim.windows([self.a, self.b], keyguard=True), wdisplays=sim.wdisplays(None, None, True),
                    input=sim.inputdump([self.a, self.b], None, True))
        self.assertEqual(C.eval_locked(events, snap, (21,), 20).status, INCOMPLETE)

    def test_occlusion(self):
        snap = Snap(activities=sim.activities([self.a, self.b], keyguard=True, occluded=False),
                    windows=sim.windows([self.a, self.b], keyguard=True, leak=20))
        self.assertEqual(C.eval_occlusion(snap, 20).status, FAIL)
        snap = Snap(activities=sim.activities([self.a, self.b], keyguard=True, occluded=True),
                    windows=sim.windows([self.a, self.b], keyguard=True, leak=20))
        self.assertEqual(C.eval_occlusion(snap, 20).status, PASS)

    def test_unlocked(self):
        self.assertEqual(C.eval_unlocked(snap_for([self.a, self.b]), {20: "A", 21: "B"}).status, PASS)
        self.assertEqual(C.eval_unlocked(snap_for([self.a, self.b], keyguard=True), {20: "A", 21: "B"}).status,
                         INCOMPLETE)
        self.assertEqual(C.eval_unlocked(snap_for([self.a, self.b]), {20: "B", 21: "B"}).status, FAIL)


class Invariants(unittest.TestCase):
    def test_lost_task_and_stranded_flag(self):
        a, b = Task(20, "ProbeTestActivity", "A"), Task(21, "ProbeOtherTestActivity", "B")
        self.assertEqual(C.eval_invariants(snap_for([a, b]), {20: "A", 21: "B"}, expect_sides=True).status, PASS)
        self.assertEqual(C.eval_invariants(snap_for([a]), {20: "A", 21: "B"}).status, FAIL)
        b.hidden = 8
        self.assertEqual(C.eval_invariants(snap_for([a, b]), {20: "A", 21: "B"}).status, FAIL)
        self.assertEqual(C.eval_invariants(snap_for([a, b]), {20: "A", 21: "B"}, allowed_hidden=(21,)).status, PASS)

    def test_panel_off(self):
        off = Snap(containers=sim.fixture("off-containers.txt"))
        self.assertEqual(C.eval_panel_off(off, {"sys.duode.panels.active": "0",
                                                "persist.debug.phh.duo.panels": "0"}).status, PASS)
        self.assertEqual(C.eval_panel_off(off, {"sys.duode.panels.active": "1"}).status, FAIL)
        on = Snap(containers=sim.fixture("panel-containers.txt"))
        self.assertEqual(C.eval_panel_off(on, {"sys.duode.panels.active": "0"}).status, FAIL)


class Crashes(unittest.TestCase):
    def test_dropbox_and_log(self):
        from duogate.parsers import DropboxEntry
        since = "2026-11-04 12:00:00"
        old = DropboxEntry("2026-11-04 11:00:00", "system_server_crash", "Process: system_server\n")
        new = DropboxEntry("2026-11-04 12:30:00", "system_server_crash", "Process: system_server\n")
        app = DropboxEntry("2026-11-04 12:30:00", "system_app_crash", "Process: com.android.settings\n")
        ui = DropboxEntry("2026-11-04 12:30:00", "system_app_crash", "Process: com.android.systemui\n")
        self.assertEqual(C.eval_crashes([old], since, []).status, PASS)
        self.assertEqual(C.eval_crashes([new], since, []).status, FAIL)
        v = C.eval_crashes([app], since, [])
        self.assertEqual(v.status, PASS)
        self.assertTrue(v.notes)
        self.assertEqual(C.eval_crashes([ui], since, []).status, FAIL)
        lines = parse_logcat("11-05 10:00:00.000  4003  4003 E AndroidRuntime: Process: com.android.systemui, PID: 4003\n"
                             "11-05 10:00:01.000  1000  1100 E ActivityManager: ANR in com.example.app\n")
        self.assertEqual(C.eval_crashes([], since, lines).status, FAIL)
        self.assertEqual(C.eval_crashes([], since, lines[1:]).status, PASS)


class CheckModel(unittest.TestCase):
    def test_missing_repetitions_are_incomplete(self):
        c = Check("x", "G", "t", required=3)
        c.add(Verdict())
        c.add(Verdict())
        self.assertEqual(c.status, INCOMPLETE)
        c.add(Verdict())
        self.assertEqual(c.status, PASS)

    def test_one_failure_fails_and_one_gap_is_incomplete(self):
        c = Check("x", "G", "t", required=2)
        c.add(Verdict())
        c.add(Verdict().incomplete("lost"))
        self.assertEqual(c.status, INCOMPLETE)
        c.add(Verdict().fail("bad"))
        self.assertEqual(c.status, FAIL)

    def test_manual_checks_never_pass(self):
        c = Check("x", "G", "t", required=1, manual=True)
        c.add(Verdict())
        self.assertEqual(c.status, INCOMPLETE)
        self.assertIn("manual review required", c.summary_reason())
        c.add(Verdict().fail("operator saw a leak"))
        self.assertEqual(c.status, FAIL)

    def test_skipped_is_incomplete(self):
        self.assertEqual(Check("x", "G", "t").skip("no PIN").status, INCOMPLETE)


if __name__ == "__main__":
    unittest.main()
