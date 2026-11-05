"""Infrastructure: secrets never logged, markers prove a live capture, restoration, reporting, and a full CLI run
against the simulated device (preflight -> gates -> restore -> report)."""
import json
import os
import shutil
import sys
import tempfile
import unittest

from duogate import cli, preconditions, report
from duogate.adb import Adb, AdbError
from duogate.device import Device
from duogate.logstream import LogBuffer
from duogate.model import EvidenceError, FAIL, INCOMPLETE, PASS

from tests.sim import FakeAdb, Sim, marker


class Secrets(unittest.TestCase):
    def test_pin_never_reaches_the_command_log_or_errors(self):
        lines = []
        adb = Adb(None, sys.executable, lines.append)      # "python shell ..." fails: a real failing subprocess
        with self.assertRaises(AdbError) as cm:
            adb.shell("input text 4711", secret=True)
        self.assertNotIn("4711", str(cm.exception))
        self.assertTrue(lines)
        self.assertFalse(any("4711" in l for l in lines), lines)
        adb.shell("input text 4711", secret=True, check=False)
        self.assertFalse(any("4711" in l for l in lines), lines)

    def test_device_types_only_plain_tokens(self):
        tmp = tempfile.mkdtemp()
        try:
            dev = Device(FakeAdb(), tmp, "t", live=False)
            with self.assertRaises(ValueError):
                dev.type_token("hello world; rm -rf /")
            self.assertRegex(dev.sentinel(), r"^dgs[0-9a-f]{8}$")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class Markers(unittest.TestCase):
    def test_marker_that_never_arrives_means_dead_capture(self):
        b = LogBuffer()
        with self.assertRaises(EvidenceError):
            b.wait_marker("run.1", timeout=0.1)

    def test_events_are_bounded_by_markers(self):
        b = LogBuffer()
        b.feed(marker("r.1"))
        b.feed(marker("r.1e"))
        b.feed(marker("r.2"))
        self.assertEqual(b.find_marker("r.1"), 0)
        self.assertEqual(b.between(0, 1), [])

    def test_unrelated_log_lines_are_not_kept(self):
        b = LogBuffer()
        b.feed("11-05 10:00:00.000  1234  1234 I Chatty  : user@example.com typed something")
        b.feed("11-05 10:00:00.000  1234  1234 W ActivityManager: Slow operation")
        b.feed("11-05 10:00:00.000  1234  1234 E ActivityManager: ANR in com.android.systemui")
        self.assertEqual([l.tag for l in b.lines], ["ActivityManager"])


class Restore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fake = FakeAdb()
        self.dev = Device(self.fake, self.tmp, "t", live=False)
        self.fake.sim = Sim(self.dev.log)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_settings_and_home_are_put_back(self):
        sim = self.fake.sim
        data = {"home": "com.android.launcher3/.uioverrides.QuickstepLauncher",
                "settings": {"secure/show_ime_with_hard_keyboard": "0", "global/stay_on_while_plugged_in": None},
                "panels": {"persist.debug.phh.duo.panels": "1"}}
        sim.settings[("secure", "show_ime_with_hard_keyboard")] = "1"
        sim.settings[("global", "stay_on_while_plugged_in")] = "7"
        sim.home_component = "org.duode.probe/.ProbeHomeActivity"
        v = preconditions.restore(self.dev, data)
        self.assertEqual(v.status, PASS, v.reasons)
        self.assertEqual(sim.settings[("secure", "show_ime_with_hard_keyboard")], "0")
        self.assertNotIn(("global", "stay_on_while_plugged_in"), sim.settings)
        self.assertEqual(sim.home_component, data["home"])

    def test_panel_property_mismatch_is_reported(self):
        self.fake.sim.props["persist.debug.phh.duo.panels"] = "0"
        v = preconditions.restore(self.dev, {"home": "a.b/.C", "settings": {},
                                             "panels": {"persist.debug.phh.duo.panels": "1"}})
        self.assertEqual(v.status, FAIL)

    def test_unknown_home_is_not_guessed(self):
        v = preconditions.restore(self.dev, {"home": "android/com.android.internal.app.ResolverActivity",
                                             "settings": {}, "panels": {}})
        self.assertEqual(v.status, INCOMPLETE)


class Reporting(unittest.TestCase):
    def test_gate_verdicts(self):
        checks = [{"gate": "G4", "manual": False, "status": PASS}, {"gate": "G4", "manual": True, "status": INCOMPLETE},
                  {"gate": "G3", "manual": True, "status": FAIL}]
        g = report.gate_verdicts(checks, ["G4", "G3", "G6"], {"G6": "preflight not clean"})
        self.assertEqual((g["G4"]["status"], g["G4"]["manual_pending"]), (PASS, 1))
        self.assertEqual(g["G3"]["status"], FAIL)
        self.assertEqual(g["G6"]["status"], INCOMPLETE)
        self.assertEqual(report.overall(g, {"status": PASS}, {"status": PASS}), FAIL)
        self.assertEqual(report.overall({"G4": g["G4"]}, {"status": PASS}, {"status": FAIL}), INCOMPLETE)


class CliRun(unittest.TestCase):
    """The whole command on the simulated device."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._adb, self._device = cli.Adb, cli.Device
        self.fakes = []

        def make_adb(serial, adb, log):
            f = FakeAdb()
            self.fakes.append(f)
            return f

        def make_device(adb, outdir, run_id, **kw):
            dev = Device(adb, outdir, run_id, settle=0.05, quiet=0, screens="none", live=False)
            dev.remove_wait = 0
            adb.sim = Sim(dev.log)
            return dev
        cli.Adb, cli.Device = make_adb, make_device

    def tearDown(self):
        cli.Adb, cli.Device = self._adb, self._device
        shutil.rmtree(self.tmp, ignore_errors=True)

    def results(self):
        run = [d for d in os.listdir(self.tmp) if d.startswith("duogate-")]
        self.assertEqual(len(run), 1)
        with open(os.path.join(self.tmp, run[0], "results.json"), encoding="utf-8") as f:
            return json.load(f), os.path.join(self.tmp, run[0])

    def test_run_restores_and_reports(self):
        rc = cli.main(["run", "G1", "G2", "G9", "--reps", "1", "--no-input", "--out", self.tmp, "--settle", "0.05",
                       "--screens", "none"])
        r, outdir = self.results()
        self.assertEqual(r["preflight"]["status"], PASS, r["preflight"])
        self.assertEqual(r["gates"]["G1"]["status"], PASS, r["gates"])
        self.assertEqual(r["gates"]["G2"]["status"], PASS)
        self.assertEqual(r["gates"]["G9"]["status"], PASS)
        self.assertEqual(r["gates"]["G1"]["manual_pending"], 1)
        self.assertEqual(r["restore"]["status"], PASS, r["restore"])
        self.assertEqual(rc, 0)
        sim = self.fakes[0].sim
        self.assertEqual(sim.home_component, "com.android.launcher3/.uioverrides.QuickstepLauncher")
        self.assertEqual(sim.settings[("secure", "show_ime_with_hard_keyboard")], "0")
        for name in ("preconditions.json", "restore.json", "report.md", "results.json"):
            self.assertTrue(os.path.exists(os.path.join(outdir, name)), name)
        with open(os.path.join(outdir, "preconditions.json"), encoding="utf-8") as f:
            pre = json.load(f)
        self.assertEqual(pre["panels"]["areas"], ["Panel0", "Panel0Tasks", "Panel1", "Panel1Tasks"])
        self.assertTrue(pre["test_settings"])
        # the IME setting is recorded, never overridden (it could mask IME defects)
        self.assertEqual(pre["ime"]["show_ime_with_hard_keyboard"], "0")
        self.assertFalse(any("show_ime" in s for s in pre["test_settings"]), pre["test_settings"])
        self.assertFalse(any("show_ime_with_hard_keyboard" in c for c in sim.commands if c.startswith("settings put")))

    def test_ime_setting_is_never_a_test_setting(self):
        self.assertNotIn("show_ime_with_hard_keyboard", [k for _, k, _, _ in preconditions.TEST_SETTINGS])

    def test_old_probe_stops_the_run_before_any_change(self):
        orig = Sim.shell

        def shell(sim, cmd):
            if cmd == "dumpsys package org.duode.probe":
                return "    versionName=16\n    codePath=/system/app/DuoProbe\n"
            return orig(sim, cmd)
        Sim.shell = shell
        try:
            rc = cli.main(["run", "G1", "--reps", "1", "--no-input", "--out", self.tmp])
        finally:
            Sim.shell = orig
        r, _ = self.results()
        self.assertEqual(rc, 2)
        self.assertEqual(r["preflight"]["status"], INCOMPLETE)
        self.assertEqual(r["gates"]["G1"]["status"], INCOMPLETE)
        self.assertEqual(self.fakes[0].sim.settings[("secure", "show_ime_with_hard_keyboard")], "0")


if __name__ == "__main__":
    unittest.main()
