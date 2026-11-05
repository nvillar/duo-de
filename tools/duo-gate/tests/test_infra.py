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
from duogate.model import EvidenceError, FAIL, INCOMPLETE, PASS, Rect

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

    def test_panel_property_mismatch_is_restored_and_needs_a_reboot(self):
        sim = self.fake.sim
        sim.props["persist.debug.phh.duo.panels"] = "0"
        data = {"home": "a.b/.C", "settings": {}, "panels": {"persist.debug.phh.duo.panels": "1"}, "active": "1"}
        v = preconditions.restore(self.dev, data)
        self.assertEqual(v.status, INCOMPLETE, v.reasons)
        self.assertTrue(any("--reboot" in r for r in v.reasons), v.reasons)
        self.assertEqual(sim.props["persist.debug.phh.duo.panels"], "1")
        self.assertEqual(sim.reboots, 0)

    def test_authorized_reboot_applies_the_restored_panel_properties(self):
        sim = self.fake.sim
        sim.props["persist.debug.phh.duo.panels"] = "0"
        sim.props["sys.duode.panels.active"] = "0"
        preconditions.REBOOT_SETTLE, old = 0, preconditions.REBOOT_SETTLE
        try:
            v = preconditions.restore(self.dev, {"home": "a.b/.C", "settings": {},
                                                 "panels": {"persist.debug.phh.duo.panels": "1"}, "active": "1"},
                                      reboot=True)
        finally:
            preconditions.REBOOT_SETTLE = old
        self.assertEqual(v.status, PASS, v.reasons)
        self.assertEqual((sim.reboots, sim.props["sys.duode.panels.active"]), (1, "1"))

    def test_panel_mode_of_this_boot_differs_from_the_recorded_one(self):
        # properties already right, but the boot still runs the other mode (e.g. PO interrupted before its reboot)
        self.fake.sim.props["sys.duode.panels.active"] = "0"
        v = preconditions.restore(self.dev, {"home": "a.b/.C", "settings": {},
                                             "panels": {"persist.debug.phh.duo.panels": "1"}, "active": "1"})
        self.assertEqual(v.status, INCOMPLETE)

    def test_persist_sys_without_root_cannot_be_restored(self):
        self.fake.sim.props["persist.sys.phh.duo.panels"] = "0"
        v = preconditions.restore(self.dev, {"home": "a.b/.C", "settings": {},
                                             "panels": {"persist.sys.phh.duo.panels": "1"}})
        self.assertEqual(v.status, FAIL)

    def test_legacy_record_cannot_forget_a_pending_reboot(self):
        # record from <= 8a97d66 (no 'active'); PO was interrupted: properties off, panel mode off this boot
        sim = self.fake.sim
        sim.props["persist.debug.phh.duo.panels"] = "0"
        sim.props["sys.duode.panels.active"] = "0"
        legacy = {"home": "a.b/.C", "settings": {}, "panels": {"persist.sys.phh.duo.panels": "",
                                                              "persist.debug.phh.duo.panels": "1"}}
        first = preconditions.restore(self.dev, dict(legacy))
        self.assertEqual(first.status, INCOMPLETE, first.reasons)
        self.assertEqual(sim.props["persist.debug.phh.duo.panels"], "1")
        # the second invocation finds the properties already right but must still apply the panel mode
        preconditions.REBOOT_SETTLE, old = 0, preconditions.REBOOT_SETTLE
        try:
            second = preconditions.restore(self.dev, dict(legacy), reboot=True)
        finally:
            preconditions.REBOOT_SETTLE = old
        self.assertEqual(second.status, PASS, second.reasons)
        self.assertEqual((sim.reboots, sim.props["sys.duode.panels.active"]), (1, "1"))
        # and without authorization it never reports complete while the mode is not applied
        sim.props["sys.duode.panels.active"] = "0"
        self.assertEqual(preconditions.restore(self.dev, dict(legacy)).status, INCOMPLETE)

    def test_legacy_record_takes_the_mode_from_preconditions(self):
        with open(os.path.join(self.tmp, "restore.json"), "w", encoding="utf-8") as f:
            json.dump({"home": "a.b/.C", "settings": {}, "panels": {"persist.debug.phh.duo.panels": "1"}}, f)
        with open(os.path.join(self.tmp, "preconditions.json"), "w", encoding="utf-8") as f:
            json.dump({"panels": {"sys.duode.panels.active": "1"}}, f)
        data = preconditions.load_restore(self.tmp)
        self.assertEqual(preconditions.expected_active(data)[0], "1")
        self.assertIn("preconditions.json", preconditions.expected_active(data)[1])

    def test_unknown_mode_is_never_complete(self):
        v = preconditions.restore(self.dev, {"home": "a.b/.C", "settings": {}})
        self.assertEqual(v.status, INCOMPLETE)

    def test_unknown_home_is_not_guessed(self):
        v = preconditions.restore(self.dev, {"home": "android/com.android.internal.app.ResolverActivity",
                                             "settings": {}, "panels": {}})
        self.assertEqual(v.status, INCOMPLETE)


class UiFallbackPrivacy(unittest.TestCase):
    def test_raw_hierarchy_is_never_stored(self):
        tmp = tempfile.mkdtemp()
        try:
            fake = FakeAdb()
            dev = Device(fake, tmp, "t", live=False)
            fake.sim = Sim(dev.log)
            personal = ('<node index="9" text="jane.doe@example.com" resource-id="" class="android.widget.EditText" '
                        'package="com.example.mail" content-desc="Inbox of Jane" checkable="false" checked="false" '
                        'clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" '
                        'long-clickable="true" password="false" selected="false" bounds="[0,0][100,100]" />')
            fake.sim.ui_xml = fake.sim.ui_xml.replace("</hierarchy>", personal + "</hierarchy>")
            targets = dev.home_targets_from_ui()
            self.assertEqual(targets["A.probeTest"], Rect(40, 421, 1310, 581))
            self.assertIn("rm -f /data/local/tmp/duogate-ui.xml", fake.sim.commands)
            stored = ""
            for root, _, files in os.walk(tmp):
                for name in files:
                    with open(os.path.join(root, name), encoding="utf-8", errors="replace") as f:
                        stored += f.read()
            self.assertIn("A.probeTest=[40,421][1310,581]", stored)
            for leak in ("jane.doe", "Inbox of Jane", "<hierarchy", "Duo probe home"):
                self.assertNotIn(leak, stored)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


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

    def test_interrupted_run_still_restores_and_reports(self):
        orig = cli.gates.GATES["G1"]

        def interrupted(ctx):
            raise KeyboardInterrupt
        cli.gates.GATES["G1"] = interrupted
        try:
            rc = cli.main(["run", "G1", "G2", "--reps", "1", "--no-input", "--out", self.tmp, "--settle", "0.05",
                           "--screens", "none"])
        finally:
            cli.gates.GATES["G1"] = orig
        r, _ = self.results()
        sim = self.fakes[0].sim
        self.assertEqual(rc, 2)
        self.assertEqual(r["restore"]["status"], PASS, r["restore"])
        self.assertEqual(sim.home_component, "com.android.launcher3/.uioverrides.QuickstepLauncher")
        self.assertNotIn(("global", "stay_on_while_plugged_in"), sim.settings)
        self.assertIn("interrupted", r["aborted"]["G1"])
        self.assertEqual(r["gates"]["G2"]["status"], INCOMPLETE)

    def test_failed_task_cleanup_does_not_skip_restoration(self):
        orig = cli.gates.GATES["G1"]

        def breaks_cleanup(ctx):
            def lost():
                raise AdbError("device lost during adb shell: error: device 'x' not found")
            ctx.dev.remove_probe_tasks = lost
        cli.gates.GATES["G1"] = breaks_cleanup
        try:
            cli.main(["run", "G1", "--reps", "1", "--no-input", "--out", self.tmp, "--settle", "0.05",
                      "--screens", "none"])
        finally:
            cli.gates.GATES["G1"] = orig
        r, _ = self.results()
        self.assertEqual(r["restore"]["status"], PASS, r["restore"])
        self.assertTrue(any("cleanup failed" in n for n in r["restore"]["notes"]), r["restore"])
        self.assertEqual(self.fakes[0].sim.home_component, "com.android.launcher3/.uioverrides.QuickstepLauncher")
        self.assertEqual(r["gates"]["G1"]["status"], INCOMPLETE)     # a gate that recorded nothing never passes

    def test_zero_or_negative_repetitions_are_rejected(self):
        for bad in ("0", "-3", "x"):
            with self.assertRaises(SystemExit):
                cli.main(["run", "G1", "--reps", bad, "--no-input", "--out", self.tmp])
        self.assertEqual(os.listdir(self.tmp), [])

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
