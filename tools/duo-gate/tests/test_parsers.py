"""Parsers against real Duo 1 captures (tests/fixtures) and against malformed input (must raise, never half-parse)."""
import unittest

from duogate import parsers as P
from duogate.model import EvidenceError, Rect

from tests.sim import fixture


class Containers(unittest.TestCase):
    def setUp(self):
        self.c = P.parse_containers(fixture("panel-containers.txt"))

    def test_panel_areas_and_bounds(self):
        self.assertEqual(self.c.panel_names(), ["Panel0", "Panel0Tasks", "Panel1", "Panel1Tasks"])
        self.assertEqual(self.c.find("Panel0Tasks").bounds, Rect(0, 0, 1350, 1800))
        self.assertEqual(self.c.find("Panel1Tasks").bounds, Rect(1434, 0, 2784, 1800))

    def test_task_parent_area(self):
        self.assertEqual(self.c.task_display_area(self.c.task(17)).name, "Panel0Tasks")
        self.assertEqual(self.c.task_display_area(self.c.task(18)).name, "Panel1Tasks")
        self.assertEqual(self.c.task_display_area(self.c.task(16)).name, "DefaultTaskDisplayArea")
        self.assertEqual(self.c.task(17).mode, "fullscreen")
        self.assertEqual(self.c.tasks_with_component("com.android.calendar/"), [17])

    def test_ime_container_and_tokens(self):
        ime = self.c.ime_container()
        self.assertEqual(ime.bounds, Rect(1434, 0, 2784, 1800))
        self.assertEqual(ime.requested_bounds, Rect(1434, 0, 2784, 1800))
        names = [n.name for n in self.c.nodes]
        self.assertIn("WindowToken{2ce343d type=2024 android.os.BinderProxy@fe20efc}", names)
        self.assertIn("Task=3(SplitRoot)", names)

    def test_panel_off_capture_has_no_panels(self):
        c = P.parse_containers(fixture("off-containers.txt"))
        self.assertEqual(c.panel_names(), [])
        self.assertIsNotNone(c.find("DefaultTaskDisplayArea"))

    def test_malformed_input_raises(self):
        text = fixture("panel-containers.txt")
        for bad in ("", "Error: unknown command\n", text.replace(" bounds=[0,0][1350,1800]", " bounds=?", 1),
                    text.replace("└─ ROOT", "└─ NOTROOT", 1)):
            with self.assertRaises(EvidenceError):
                P.parse_containers(bad)

    def test_duplicate_task_is_ambiguous(self):
        text = fixture("panel-containers.txt").replace("Task=18 ", "Task=17 ")
        c = P.parse_containers(text)
        with self.assertRaises(EvidenceError):
            c.task(17)


class Activities(unittest.TestCase):
    def test_real_capture(self):
        a = P.parse_activities(fixture("off-activities.txt"))
        self.assertEqual(sorted(a.tasks), [1, 3, 4, 5, 6, 7, 8, 9, 12])
        cal = a.task(7)
        self.assertEqual(cal.activities[0].component, "com.android.calendar/.AllInOneActivity")
        self.assertEqual(cal.activities[0].state, "STOPPED")
        self.assertEqual(cal.activities[0].source_bounds, Rect(717, 1540, 867, 1709))
        self.assertFalse(cal.visible)
        self.assertEqual(cal.force_hidden, 0)
        self.assertEqual(a.resumed_task_ids, [6])
        self.assertTrue(a.keyguard_showing)
        self.assertFalse(a.keyguard_occluded)

    def test_force_hidden_values(self):
        # TaskFragment.dump prints the int in decimal (android-16.0.0_r4)
        for raw, want in (("8", 8), ("2", 2), ("10", 10), ("12", 12)):
            self.assertEqual(P._force_hidden(raw), want, raw)
        for raw in ("a", "0x8", "-1", "?"):
            with self.assertRaises(EvidenceError):
                P._force_hidden(raw)

    def test_not_an_activities_dump(self):
        with self.assertRaises(EvidenceError):
            P.parse_activities("Can't find service: activity\n")


class WindowAndInput(unittest.TestCase):
    def test_window_displays(self):
        d = P.parse_window_displays(fixture("off-window-displays.txt"))
        self.assertEqual(d.current_focus.hash, "671cb1b")
        self.assertEqual(d.current_focus.title, "NotificationShade")
        self.assertEqual(d.focused_app_task, 6)
        self.assertIsNone(d.ime_layering_target)
        self.assertEqual(d.ime_input_target.hash, "51d9e08")
        with self.assertRaises(EvidenceError):
            P.parse_window_displays(fixture("off-window-displays.txt"), display_id=1)

    def test_window_windows(self):
        w = P.parse_window_windows(fixture("off-window-windows.txt"))
        self.assertEqual(w["51d9e08"].task_id, 6)
        self.assertEqual(w["51d9e08"].package, "com.android.launcher3")
        self.assertFalse(w["51d9e08"].on_screen)
        self.assertEqual(w["51d9e08"].frame, Rect(0, 0, 2784, 1800))
        self.assertIsNone(w["671cb1b"].task_id)
        self.assertEqual(w["671cb1b"].package, "com.android.systemui")
        self.assertTrue(w["671cb1b"].on_screen)

    def test_input(self):
        i = P.parse_input(fixture("off-input.txt"))
        self.assertEqual(i.focused_window_hash, "671cb1b")
        self.assertEqual(i.focused_app_task, 6)
        shade = next(x for x in i.windows if x.hash == "671cb1b")
        self.assertIn("NOT_TOUCHABLE", shade.flags)
        self.assertFalse(shade.touchable)
        with self.assertRaises(EvidenceError):
            P.parse_input("INPUT MANAGER (dumpsys input)\n")

    def test_device_state(self):
        self.assertEqual(P.parse_device_state(fixture("device-state.txt")), (2, "OPENED"))


class Small(unittest.TestCase):
    def test_home(self):
        self.assertEqual(P.parse_resolved_home(
            "priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=true\n"
            "com.android.launcher3/.uioverrides.QuickstepLauncher\n"), "com.android.launcher3/.uioverrides.QuickstepLauncher")
        with self.assertRaises(EvidenceError):
            P.parse_resolved_home("No activity found\n")

    def test_wm(self):
        self.assertEqual(P.parse_wm_size("Physical size: 2784x1800\n"), ((2784, 1800), None))
        self.assertEqual(P.parse_wm_size("Physical size: 2784x1800\nOverride size: 1350x1800\n"),
                         ((2784, 1800), (1350, 1800)))
        self.assertEqual(P.parse_wm_density("Physical density: 400\n"), (400, None))

    def test_dropbox(self):
        # DropBoxManagerService.dump with --print (r4): entry text unindented, entries separated by '=' lines
        text = ("Drop box contents: 3 entries\nMax entries: 1000\nLow priority rate limit period: 2000 ms\n"
                "Low priority tags: []\nSearching for: system_server_crash system_app_crash\n\n"
                "========================================\n"
                "2026-11-04 12:00:01 system_server_crash (compressed text, 1234 bytes)\n"
                "Process: system_server\njava.lang.NullPointerException\n"
                "========================================\n"
                "2026-11-04 12:05:00 system_app_crash (text, 99 bytes)\nProcess: com.android.systemui\n")
        e = P.parse_dropbox(text)
        self.assertEqual([(x.time, x.tag) for x in e], [("2026-11-04 12:00:01", "system_server_crash"),
                                                         ("2026-11-04 12:05:00", "system_app_crash")])
        self.assertIn("com.android.systemui", e[1].body)
        self.assertNotIn("systemui", e[0].body)
        self.assertEqual(P.parse_dropbox("Drop box contents: 0 entries\n"), [])
        with self.assertRaises(EvidenceError):
            P.parse_dropbox("")

    def test_credential(self):
        self.assertEqual(P.parse_credential_type("User State:\n  User 0\n   CredentialType: PIN\n"), "PIN")
        with self.assertRaises(EvidenceError):
            P.parse_credential_type("nothing")


class Logcat(unittest.TestCase):
    def test_threadtime_and_tag_with_space(self):
        l = P.parse_log_line("11-05 02:13:01.519  3958  3958 D POSTURE PROCESSOR: Setting display composition 0 new!")
        self.assertEqual((l.tag, l.level, l.pid), ("POSTURE PROCESSOR", "D", 3958))
        self.assertEqual(P.posture_composition(l), 0)

    def test_posture_capture(self):
        lines = P.parse_logcat(fixture("posture.log"))
        self.assertEqual([c for c in (P.posture_composition(l) for l in lines) if c is not None], [0, 1, 2])

    def test_probe_event_values_with_spaces(self):
        l = P.parse_log_line("11-05 10:00:00.000  5000  5000 I DuoProbe: event=configOnCreate act=ProbeTestActivity@1a "
                             "task=21 config={1.0 ?mcc0mnc [en_US] winConfig={ mBounds=Rect(0, 0 - 1350, 1800)}}")
        e = P.probe_event(l)
        self.assertEqual((e.event, e.act_class, e.task), ("configOnCreate", "ProbeTestActivity", 21))
        self.assertTrue(e.kv["config"].startswith("{1.0 ?mcc0mnc"))
        l = P.parse_log_line("11-05 10:00:00.000  5000  5000 I DuoProbe: event=onResume act=ProbeTestActivity@1a "
                             "task=21 display=0 rotation=0 bounds=[1434,0][2784,1800] maxBounds=[1434,0][2784,1800]")
        self.assertEqual(P.probe_event(l).rect("bounds"), Rect(1434, 0, 2784, 1800))

    def test_policy_lines(self):
        l = P.parse_log_line("11-05 10:00:00.000  1000  1200 I DuoDisplayAreaPolicy: Home: minimizing Panel1 (1 tasks)")
        self.assertEqual(P.policy_minimized_panel(l), ("Panel1", 1))
        l = P.parse_log_line("11-05 10:00:00.000  1000  1200 I DuoDisplayAreaPolicy: Restored minimized "
                             "Task{8d1f2 #21 type=standard A=10190:org.duode.probe}")
        self.assertEqual(P.policy_restored_task(l), 21)


class Uiautomator(unittest.TestCase):
    def test_probe_home_dump(self):
        nodes = P.parse_uiautomator(fixture("probe-home-uiautomator.xml"))
        buttons = [(n.text, n.bounds) for n in nodes if n.text == "Probe test"]
        self.assertEqual(buttons, [("Probe test", Rect(40, 421, 1310, 581)), ("Probe test", Rect(1474, 421, 2744, 581))])
        with self.assertRaises(EvidenceError):
            P.parse_uiautomator("ERROR: could not get idle state.")


if __name__ == "__main__":
    unittest.main()
