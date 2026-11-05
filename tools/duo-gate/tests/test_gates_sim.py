"""End-to-end gate procedures against the simulated device (tests/sim.py): the collector's own adb commands drive it.

A correct device must PASS the automated checks; injected bugs (wrong panel, Home on the focused instead of the
gestured panel, Back acting on the focused panel, missing events, touches to the wrong task, lost device) must give
FAIL or INCOMPLETE, never PASS.
"""
import shutil
import tempfile
import unittest

from duogate import gates, report
from duogate.adb import AdbError
from duogate.device import Device
from duogate.model import FAIL, INCOMPLETE, PASS
from duogate.prompts import Operator

from tests.sim import FakeAdb, Sim


class SimRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="duogate-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ctx(self, bugs=(), reps=2, lost_after=None):
        fake = FakeAdb(lost_after=lost_after)
        dev = Device(fake, self.tmp, "t", settle=0.05, quiet=0, screens="none", live=False)
        dev.remove_wait = 0
        fake.sim = Sim(dev.log, bugs)
        op = Operator(interactive=False)
        return gates.Ctx(dev, op, reps, {"navigation_mode": "2", "ime": {"show_ime_with_hard_keyboard": "0"}}), fake

    def statuses(self, ctx):
        return {c.id: c.status for c in ctx.checks}

    # -- G1 -----------------------------------------------------------------------------------------------------------
    def test_g1_on_a_correct_device(self):
        ctx, fake = self.ctx()
        gates.gate_g1(ctx)
        s = self.statuses(ctx)
        for cid in ("G1.launch.A", "G1.launch.B", "G1.launch0.A", "G1.launch0.B", "G1.crossReuse",
                    "G1.amStartDisplay0"):
            self.assertEqual(s[cid], PASS, (cid, [c.to_json() for c in ctx.checks if c.id == cid]))
        self.assertEqual(s["G1.freeformRequest"], INCOMPLETE)        # recorded only, never PASS
        self.assertTrue(any(cmd.startswith("am stack remove") for cmd in fake.sim.commands))
        launch_a = next(c for c in ctx.checks if c.id == "G1.launch.A")
        self.assertEqual(len(launch_a.attempts), 2)
        self.assertTrue(all(a.marker for a in launch_a.attempts))

    def test_g1_wrong_panel_fails(self):
        ctx, _ = self.ctx(bugs={"wrong_panel"})
        gates.gate_g1(ctx)
        s = self.statuses(ctx)
        self.assertEqual(s["G1.launch.A"], FAIL)
        self.assertEqual(s["G1.launch.B"], FAIL)

    def test_g1_reuse_creating_new_tasks_fails(self):
        ctx, _ = self.ctx(bugs={"no_reuse"})
        gates.gate_g1(ctx)
        self.assertEqual(self.statuses(ctx)["G1.launch.A"], FAIL)

    def test_g1_missing_events_is_incomplete(self):
        ctx, _ = self.ctx(bugs={"no_resume"})
        gates.gate_g1(ctx)
        s = self.statuses(ctx)
        self.assertEqual(s["G1.launch.A"], INCOMPLETE)
        self.assertNotIn(PASS, [s["G1.launch.A"], s["G1.launch.B"], s["G1.crossReuse"]])

    # -- G2 -----------------------------------------------------------------------------------------------------------
    def test_g2_on_a_correct_device(self):
        ctx, _ = self.ctx()
        gates.gate_g2(ctx)
        s = self.statuses(ctx)
        for cid in ("G2.home.A", "G2.home.B", "G2.restore.A", "G2.restore.B", "G2.crossHome.A", "G2.crossHome.B"):
            self.assertEqual(s[cid], PASS, (cid, [c.to_json() for c in ctx.checks if c.id == cid]))

    def test_g2_home_on_the_focused_panel_fails_cross_focused_check(self):
        ctx, _ = self.ctx(bugs={"home_focused_panel"})
        gates.gate_g2(ctx)
        s = self.statuses(ctx)
        self.assertEqual(s["G2.home.A"], PASS)          # focused Home is still right
        self.assertEqual(s["G2.crossHome.A"], FAIL)
        self.assertEqual(s["G2.crossHome.B"], FAIL)

    def test_g2_synthetic_gesture_ignored_without_operator_is_incomplete(self):
        ctx, _ = self.ctx(bugs={"synthetic_home_ignored"})
        gates.gate_g2(ctx)
        s = self.statuses(ctx)
        self.assertEqual(s["G2.crossHome.A"], INCOMPLETE)

    def test_g2_home_stopping_the_other_panel_fails(self):
        ctx, _ = self.ctx(bugs={"home_stops_other"})
        gates.gate_g2(ctx)
        self.assertEqual(self.statuses(ctx)["G2.home.A"], FAIL)

    def test_g2_touch_reaching_the_wrong_task_fails(self):
        ctx, _ = self.ctx(bugs={"touch_to_other"})
        gates.gate_g2(ctx)
        self.assertEqual(self.statuses(ctx)["G2.home.A"], FAIL)

    def test_g2_probe_without_touch_logging_is_incomplete(self):
        ctx, _ = self.ctx(bugs={"no_touch_log"})
        gates.gate_g2(ctx)
        s = self.statuses(ctx)
        self.assertEqual(s["G2.home.A"], INCOMPLETE)

    # -- G4 -----------------------------------------------------------------------------------------------------------
    def test_g4_on_a_correct_device(self):
        ctx, _ = self.ctx()
        gates.gate_g4(ctx)
        s = self.statuses(ctx)
        self.assertEqual(s["G4.back.A"], PASS, [c.to_json() for c in ctx.checks if c.id == "G4.back.A"])
        self.assertEqual(s["G4.back.B"], PASS)
        for cid in ("G4.hingeBack.A", "G4.backCancel.B"):
            self.assertEqual(s[cid], INCOMPLETE)         # manual, no operator

    def test_g4_back_on_the_focused_panel_fails(self):
        ctx, _ = self.ctx(bugs={"back_to_focused"})
        gates.gate_g4(ctx)
        s = self.statuses(ctx)
        self.assertEqual(s["G4.back.A"], FAIL)

    def test_g4_three_button_navigation_is_not_run(self):
        ctx, _ = self.ctx()
        ctx.pre["navigation_mode"] = "0"
        gates.gate_g4(ctx)
        self.assertEqual(self.statuses(ctx)["G4.back.A"], INCOMPLETE)

    # -- device lost / reporting ----------------------------------------------------------------------------------------
    def test_lost_device_leaves_checks_incomplete(self):
        ctx, _ = self.ctx(lost_after=15)
        with self.assertRaises(AdbError):
            gates.gate_g1(ctx)
        checks = [c.to_json() for c in ctx.checks]
        self.assertTrue(checks)
        self.assertNotIn(PASS, {c["status"] for c in checks if c["id"] == "G1.launch.A"})
        gv = report.gate_verdicts(checks, ["G1", "G2"], {"G1": "device lost", "G2": "not run"})
        self.assertEqual(gv["G1"]["status"], INCOMPLETE)
        self.assertEqual(gv["G2"]["status"], INCOMPLETE)

    def test_g5_runs_with_the_device_ime_setting(self):
        # show_ime_with_hard_keyboard=0 (device default) is not a reason to skip G5; the simulated probe has no IME,
        # so the IME checks must come out FAIL/INCOMPLETE, never PASS and never skipped
        ctx, fake = self.ctx(reps=1)
        gates.gate_g5(ctx)
        ime = [c for c in ctx.checks if c.id.startswith("G5.ime.")]
        self.assertEqual(len(ime), 4)
        self.assertTrue(all(c.skipped is None and c.attempts for c in ime), [c.to_json() for c in ime])
        self.assertTrue(all(c.status != PASS for c in ime))
        self.assertFalse(any("show_ime" in c for c in fake.sim.commands))

    # -- manual fallbacks / rotation ---------------------------------------------------------------------------------
    def interactive(self, ctx, fake):
        def answer(prompt):
            if "Swipe up from the bottom edge" in prompt:
                fake.sim.home("A" if "(A)" in prompt else "B")
            return ""
        ctx.op = Operator(interactive=True, inp=answer)

    def test_g2_real_gesture_fallback_never_turns_into_an_automated_pass(self):
        ctx, fake = self.ctx(bugs={"synthetic_home_ignored"}, reps=1)
        self.interactive(ctx, fake)
        gates.gate_g2(ctx)
        s = self.statuses(ctx)
        self.assertEqual(s["G2.crossHome.A"], INCOMPLETE)            # the synthetic attempt stays as it was
        manual = next(c for c in ctx.checks if c.id == "G2.crossHomeManual.A")
        self.assertTrue(manual.manual)
        self.assertEqual(manual.counts()[PASS], 1)                   # the evidence agreed ...
        self.assertEqual(manual.status, INCOMPLETE)                  # ... but a manual check never PASSes

    def test_g2_real_gesture_fallback_can_fail(self):
        ctx, fake = self.ctx(bugs={"synthetic_home_ignored", "home_focused_panel"}, reps=1)
        self.interactive(ctx, fake)
        gates.gate_g2(ctx)
        self.assertEqual(self.statuses(ctx)["G2.crossHomeManual.A"], FAIL)

    def test_operator_performed_checks_are_manual(self):
        ctx, _ = self.ctx()
        ctx.pre["secure_lock"] = "NONE"
        ctx.options["g6_unlock"] = "manual"
        gates.gate_g6(ctx)
        gates.gate_g1_launcher3(ctx)
        manual = {c.id: c.manual for c in ctx.checks}
        self.assertTrue(manual["G6.unlock.ordinary"])
        self.assertFalse(manual["G6.locked.ordinary"])
        self.assertTrue(manual["G1L.A"] and manual["G1L.B"])

    def test_g7_rotation_verified(self):
        ctx, _ = self.ctx(reps=1)
        gates.gate_g7(ctx)
        rot = next(c for c in ctx.checks if c.id == "G7.rotate")
        self.assertEqual(rot.status, PASS, rot.to_json())
        self.assertEqual(len(rot.attempts), 4)

    def test_g7_rotation_that_does_not_happen_is_incomplete(self):
        ctx, _ = self.ctx(bugs={"rotation_ignored"}, reps=1)
        gates.gate_g7(ctx)
        rot = next(c for c in ctx.checks if c.id == "G7.rotate")
        self.assertEqual(rot.status, INCOMPLETE)
        self.assertNotIn(PASS, {a.status for a in rot.attempts[:3]})

    def test_g6_without_secure_lock_is_not_run(self):
        ctx, _ = self.ctx()
        ctx.pre["secure_lock"] = "NONE"
        gates.gate_g6(ctx)
        self.assertTrue(ctx.checks)
        self.assertTrue(all(c.status == INCOMPLETE for c in ctx.checks))

    def test_manual_gates_without_operator_are_incomplete(self):
        ctx, _ = self.ctx()
        gates.gate_g3(ctx)
        gates.gate_g7(ctx)
        manual = [c for c in ctx.checks if c.manual]
        self.assertTrue(manual)
        self.assertTrue(all(c.status == INCOMPLETE for c in manual))


if __name__ == "__main__":
    unittest.main()
