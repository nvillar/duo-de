"""Static checks for the DuoProbe launch invariants (stdlib unittest, no Android needed).

The panel routing in frameworks_base 0008 (panelForLaunch) only routes a launch from home that is a LAUNCHER intent
with source bounds; a probe launch without CATEGORY_LAUNCHER silently bypasses it and invalidates the gates. DuoProbe
is not buildable on the host, so these tests check the source and manifest instead.
Run: uv run --no-project python -m unittest discover -s tools -p "test_*.py"
"""
import os, re, unittest
import xml.etree.ElementTree as ET

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "duo-probe", "DuoProbe")
JAVA = os.path.join(SRC, "src", "org", "duode", "probe")
A = "{http://schemas.android.com/apk/res/android}"


def read(name):
    with open(os.path.join(JAVA, name), encoding="utf-8") as f:
        return f.read()


def code(text):
    """Java source with comments and string literals removed."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r'"(\\.|[^"\\])*"', '""', text)


class LaunchIntents(unittest.TestCase):
    def test_launcher_intent_builder_is_main_plus_launcher(self):
        body = code(read("ProbeIntents.java"))
        builder = re.search(r"static Intent launcher\(.*?\{(.*?)\}", body, re.S).group(1)
        self.assertIn("new Intent(Intent.ACTION_MAIN)", builder)
        self.assertIn(".addCategory(Intent.CATEGORY_LAUNCHER)", builder)
        self.assertIn(".setComponent(component)", builder)
        check = re.search(r"static boolean isLauncherIntent\(.*?\{(.*?)\}", body, re.S).group(1)
        for needle in ("Intent.ACTION_MAIN.equals", "hasCategory(Intent.CATEGORY_LAUNCHER)", "getComponent() != null"):
            self.assertIn(needle, check)

    def test_home_builds_every_launch_with_the_builder(self):
        body = code(read("ProbeHomeActivity.java"))
        # every Intent the home creates is a query or comes from ProbeIntents.launcher
        creations = re.findall(r"new Intent\(([^)]*)\)", body)
        allowed = {"Intent.ACTION_MAIN", "intent"}   # the launcher-app query, and the per-tap copy of a built intent
        self.assertTrue(set(creations) <= allowed, creations)
        self.assertNotRegex(body, r"new Intent\(\s*(this|ProbeHomeActivity\.this)\s*,")
        self.assertNotIn(".setClass(", body)
        for activity in ("ProbeTestActivity", "ProbeOtherTestActivity", "ProbeLockedTestActivity"):
            self.assertRegex(body, r"ProbeIntents\.launcher\(new ComponentName\(this, %s\.class\)" % activity)
        self.assertGreaterEqual(body.count("ProbeIntents.launcher("), 5)

    def test_launch_refuses_non_launcher_intents(self):
        body = code(read("ProbeHomeActivity.java"))
        launch = re.search(r"private void launch\(View v.*?\{(.*?)startActivity", body, re.S).group(1)
        guard = launch.index("ProbeIntents.isLauncherIntent(intent)")
        self.assertLess(guard, launch.index("setSourceBounds"))
        self.assertIn("return;", launch[guard:launch.index("setSourceBounds")])
        self.assertNotIn("setLaunchWindowingMode", body)
        self.assertRegex(launch, r"if \(mSendDisplayId\) options\.setLaunchDisplayId")


class Manifest(unittest.TestCase):
    def setUp(self):
        self.root = ET.parse(os.path.join(SRC, "AndroidManifest.xml")).getroot()
        self.acts = {a.get(A + "name"): a for a in self.root.iter("activity")}

    def categories(self, name):
        return {c.get(A + "name") for c in self.acts[name].iter("category")}

    def test_home_is_a_home_activity(self):
        self.assertIn("android.intent.category.HOME", self.categories(".ProbeHomeActivity"))

    def test_test_activities_are_launchable_but_not_launcher_entries(self):
        for name in (".ProbeTestActivity", ".ProbeOtherTestActivity", ".ProbeLockedTestActivity"):
            self.assertEqual(self.acts[name].get(A + "exported"), "true", name)
            self.assertNotIn("android.intent.category.LAUNCHER", self.categories(name), name)
        self.assertEqual(self.acts[".ProbeLockedTestActivity"].get(A + "showWhenLocked"), "true")

    def test_each_reusable_test_activity_has_its_own_task_affinity(self):
        # one reusable probe task per panel (tools/duo-gate): the affinities must differ from each other
        affinities = [self.acts[n].get(A + "taskAffinity") for n in
                      (".ProbeTestActivity", ".ProbeOtherTestActivity", ".ProbeLockedTestActivity")]
        self.assertEqual(len(set(affinities)), 3, affinities)

    def test_no_opt_in_to_receiver_side_intent_matching(self):
        # explicit LAUNCHER intents to filter-less activities rely on this staying off (Android 16 Safer Intents)
        for el in self.root.iter():
            self.assertIsNone(el.get(A + "intentMatchingFlags"), el.tag)


class GateEvents(unittest.TestCase):
    """The events tools/duo-gate depends on, and that no editor contents are ever logged."""

    def test_touch_and_targets_events_exist(self):
        body = code(read("ProbeActivity.java"))
        self.assertRegex(body, r"public boolean dispatchTouchEvent\(MotionEvent ev\)")
        self.assertIn("return super.dispatchTouchEvent(ev);", body)
        raw = read("ProbeActivity.java")
        for needle in ('log("touch", "action="', '" rawX="', '" rawY="', 'log("targets", s)'):
            self.assertIn(needle, raw)

    def test_text_event_logs_only_sentinels_and_length(self):
        raw = read("ProbeTestActivity.java")
        self.assertIn('Pattern.compile("dgs[0-9a-f]{8}")', raw)
        watcher = raw[raw.index("class SentinelWatcher"):]
        logs = re.findall(r"log\(\"text\",(.*?)\);", watcher, re.S)
        self.assertEqual(len(logs), 1, logs)
        # only the editor number, the length and the matched sentinel tokens
        self.assertNotRegex(logs[0], r"\bs\b(?!\.length)|toString|getText")
        self.assertIn("s.length()", logs[0])
        self.assertIn('String.join(",", found)', logs[0])

    def test_test_window_registers_its_targets(self):
        body = read("ProbeTestActivity.java")
        for name in ("title", "editor1", "editor2", "showIme", "hideIme"):
            self.assertIn('registerTarget("%s"' % name, body)
        home = read("ProbeHomeActivity.java")
        for name in ("toggle", "probeTest", "probeTest2", "probeTestNew", "probeLocked"):
            self.assertRegex(home, r'side \+ "\.%s"' % name)


if __name__ == "__main__":
    unittest.main()
