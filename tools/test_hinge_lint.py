"""Tests for hinge_lint (stdlib unittest, no device). Run: uv run --no-project python -m unittest discover -s tools -p "test_*.py" """
import contextlib, io, json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hinge_lint as hl  # noqa: E402


def dump(*nodes, rotation=0):
    """A minimal uiautomator dump; each node is (bounds, attrs dict)."""
    body = "".join(
        f'<node bounds="[{b[0]},{b[1]}][{b[2]},{b[3]}]" package="{a.get("package", "app")}"'
        f' class="android.widget.TextView" text="{a.get("text", "")}" content-desc=""'
        f' clickable="{str(a.get("clickable", False)).lower()}" resource-id="" />'
        for b, a in nodes)
    return f'<?xml version="1.0"?><hierarchy rotation="{rotation}">{body}</hierarchy>'


class Geometry(unittest.TestCase):
    def test_duo1_spanned_all_orientations(self):
        self.assertEqual(hl.hinge_rect(2784, 1800), ("duo", hl.SPANNED, (1350, 0, 1434, 1800)))
        self.assertEqual(hl.hinge_rect(1800, 2784), ("duo", hl.SPANNED, (0, 1350, 1800, 1434)))

    def test_duo1_gapless_is_a_zero_width_seam(self):
        self.assertEqual(hl.hinge_rect(2700, 1800), ("duo", hl.GAPLESS, (1350, 0, 1350, 1800)))
        self.assertEqual(hl.hinge_rect(1800, 2700), ("duo", hl.GAPLESS, (0, 1350, 1800, 1350)))

    def test_duo1_single_panel(self):
        self.assertEqual(hl.hinge_rect(1350, 1800), ("duo", hl.SINGLE, None))
        self.assertEqual(hl.hinge_rect(1800, 1350), ("duo", hl.SINGLE, None))

    def test_duo2_unchanged(self):
        self.assertEqual(hl.hinge_rect(2754, 1892), ("duo2", hl.SPANNED, (1344, 0, 1410, 1892)))
        self.assertEqual(hl.hinge_rect(1892, 2754), ("duo2", hl.SPANNED, (0, 1344, 1892, 1410)))
        self.assertEqual(hl.hinge_rect(2688, 1892), ("duo2", hl.GAPLESS, (1344, 0, 1344, 1892)))
        self.assertEqual(hl.hinge_rect(1344, 1892), ("duo2", hl.SINGLE, None))

    def test_unknown_sizes(self):
        for w, h in [(1080, 1920), (2600, 1892), (2754, 1800), (2784, 1892), (1392, 1800), (0, 0)]:
            self.assertEqual(hl.hinge_rect(w, h), (None, hl.UNKNOWN, None), (w, h))

    def test_device_filter(self):
        self.assertEqual(hl.hinge_rect(2784, 1800, ["duo2"]), (None, hl.UNKNOWN, None))
        self.assertEqual(hl.hinge_rect(2784, 1800, ["duo"])[1], hl.SPANNED)

    def test_band_matches_the_physical_panels(self):
        # the band sits exactly between the two panels for every known spanned layout
        for device, (panel, hinge, height) in hl.SPECS.items():
            _, _, band = hl.hinge_rect(2 * panel + hinge, height)
            self.assertEqual(band[2] - band[0], hinge, device)
            self.assertEqual(2 * panel + hinge - band[2], panel, device)


class Intersection(unittest.TestCase):
    def test_band(self):
        band = (1350, 0, 1434, 1800)
        self.assertTrue(hl.intersects((1300, 0, 1360, 10), band))
        self.assertFalse(hl.intersects((0, 0, 1350, 1800), band))    # touching is not overlapping
        self.assertFalse(hl.intersects((1434, 0, 2784, 1800), band))

    def test_seam_only_when_crossed(self):
        seam = (1350, 0, 1350, 1800)
        self.assertTrue(hl.intersects((1300, 0, 1400, 10), seam))
        self.assertFalse(hl.intersects((0, 0, 1350, 1800), seam))    # flush with the seam
        self.assertFalse(hl.intersects((1350, 0, 2700, 1800), seam))
        rotated = (0, 1350, 1800, 1350)
        self.assertTrue(hl.intersects((0, 1300, 10, 1400), rotated))
        self.assertFalse(hl.intersects((0, 0, 1800, 1350), rotated))


class Lint(unittest.TestCase):
    def test_duo1_spanned_hits(self):
        xml = dump(((1300, 100, 1400, 150), {"text": "Crosses"}),
                   ((1360, 200, 1420, 250), {"clickable": True}),
                   ((0, 0, 2784, 1800), {}),                       # backdrop across both panels: ignored
                   ((0, 300, 1350, 400), {"text": "Left only"}))
        hits = hl.lint(xml, (1350, 0, 1434, 1800), set())
        self.assertEqual([h["bounds"] for h in hits], [[1300, 100, 1400, 150], [1360, 200, 1420, 250]])
        self.assertEqual([h["inside_hinge"] for h in hits], [False, True])

    def test_gapless_seam_hits(self):
        xml = dump(((1300, 100, 1400, 150), {"text": "Crosses"}), ((0, 300, 1350, 400), {"text": "Flush"}))
        hits = hl.lint(xml, (1350, 0, 1350, 1800), set())
        self.assertEqual([h["text"] for h in hits], ["Crosses"])

    # Regression (review): a zero-width seam made every crossing node "span" it, and spanning nodes without text were
    # dropped, so clickable controls across a gapless seam were never reported.
    def test_gapless_clickable_leaf_without_text_is_reported(self):
        xml = dump(((1300, 100, 1400, 150), {"clickable": True}))
        self.assertEqual([h["bounds"] for h in hl.lint(xml, (1350, 0, 1350, 1800), set())], [[1300, 100, 1400, 150]])

    def test_gapless_rotated_clickable_leaf_without_text_is_reported(self):
        xml = dump(((100, 1300, 150, 1400), {"clickable": True}), rotation=1)
        self.assertEqual([h["bounds"] for h in hl.lint(xml, (0, 1350, 1800, 1350), set())], [[100, 1300, 150, 1400]])

    def test_gapless_passive_icon_crossing_the_seam_is_reported(self):
        xml = dump(((1320, 100, 1380, 160), {}))
        self.assertEqual(len(hl.lint(xml, (1350, 0, 1350, 1800), set())), 1)

    def test_real_hinge_clickable_spanning_controls_are_reported(self):
        band = (1350, 0, 1434, 1800)
        xml = dump(((1300, 100, 1500, 150), {"clickable": True}),      # icon button across the whole hinge
                   ((0, 200, 2784, 300), {"clickable": True}),          # full-width row across both panels
                   ((1340, 400, 1440, 450), {}))                         # passive leaf covering the hinge
        hits = hl.lint(xml, band, set())
        self.assertEqual([h["bounds"] for h in hits],
                         [[1300, 100, 1500, 150], [0, 200, 2784, 300], [1340, 400, 1440, 450]])
        self.assertTrue(all(h["clickable"] for h in hits[:2]))

    def test_real_hinge_rotated_clickable_spanning_control_is_reported(self):
        xml = dump(((100, 1300, 150, 1500), {"clickable": True}), rotation=1)
        self.assertEqual(len(hl.lint(xml, (0, 1350, 1800, 1434), set())), 1)

    def test_passive_backdrops_and_containers_are_exempt(self):
        xml = ('<hierarchy rotation="0">'
               '<node bounds="[0,0][2700,1800]" package="app" text="" content-desc="" clickable="false">'
               '<node bounds="[0,0][2700,1800]" package="app" text="" content-desc="" clickable="false" />'
               '<node bounds="[600,500][2200,600]" package="app" text="" content-desc="" clickable="false" />'
               '</node></hierarchy>')
        self.assertEqual(hl.lint(xml, (1350, 0, 1350, 1800), set()), [])   # container + 2 backdrops
        self.assertEqual(hl.lint(xml, (1350, 0, 1434, 1800), set()), [])

    def test_long_clickable_and_checkable_count_as_controls(self):
        xml = ('<hierarchy><node bounds="[1300,0][1400,50]" package="a" long-clickable="true" />'
               '<node bounds="[1300,60][1400,90]" package="a" checkable="true"><node bounds="[0,0][1,1]" /></node>'
               '</hierarchy>')
        self.assertEqual(len(hl.lint(xml, (1350, 0, 1350, 1800), set())), 2)


class Cli(unittest.TestCase):
    def run_cli(self, xml, *args):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "dump.xml")
            with open(p, "w", encoding="utf-8") as f:
                f.write(xml)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = hl.main(["--xml", p, *args])
            return code, out.getvalue(), err.getvalue()

    def test_unknown_geometry_is_an_explicit_unsupported_exit(self):
        code, out, err = self.run_cli(dump(), "--width", "1080", "--height", "1920")
        self.assertEqual(code, hl.EXIT_UNSUPPORTED)
        self.assertIn("unsupported geometry", err)
        self.assertEqual(out, "")

    def test_unknown_geometry_json(self):
        code, out, _ = self.run_cli(dump(), "--width", "2600", "--height", "1892", "--json")
        self.assertEqual(code, hl.EXIT_UNSUPPORTED)
        self.assertEqual(json.loads(out)["mode"], hl.UNKNOWN)

    def test_duo1_spanned_overlap_exit(self):
        xml = dump(((1300, 100, 1400, 150), {"text": "Crosses"}))
        code, out, _ = self.run_cli(xml, "--width", "2784", "--height", "1800")
        self.assertEqual(code, hl.EXIT_OVERLAPS)
        self.assertIn("duo spanned", out)

    def test_duo1_rotated_clean_exit(self):
        xml = dump(((0, 0, 1800, 1350), {"text": "Top panel"}), rotation=1)
        code, out, _ = self.run_cli(xml, "--width", "1800", "--height", "2784")
        self.assertEqual(code, hl.EXIT_CLEAN)
        self.assertIn("0 overlap(s)", out)

    def test_gapless_clickable_leaf_exits_with_overlaps(self):
        # the reproduction from the review: was exit 0 with no hits, in both orientations
        code, out, _ = self.run_cli(dump(((1300, 100, 1400, 150), {"clickable": True})),
                                    "--width", "2700", "--height", "1800", "--json")
        self.assertEqual(code, hl.EXIT_OVERLAPS)
        self.assertEqual(json.loads(out)["hits"][0]["bounds"], [1300, 100, 1400, 150])
        code, out, _ = self.run_cli(dump(((100, 1300, 150, 1400), {"clickable": True}), rotation=1),
                                    "--width", "1800", "--height", "2700", "--json")
        self.assertEqual(code, hl.EXIT_OVERLAPS)
        self.assertEqual(json.loads(out)["hits"][0]["bounds"], [100, 1300, 150, 1400])

    def test_single_panel_is_clean(self):
        code, out, _ = self.run_cli(dump(), "--width", "1350", "--height", "1800")
        self.assertEqual(code, hl.EXIT_CLEAN)
        self.assertIn("single panel", out)

    def test_device_filter_rejects_the_other_duo(self):
        code, _, _ = self.run_cli(dump(), "--width", "2754", "--height", "1892", "--device", "duo")
        self.assertEqual(code, hl.EXIT_UNSUPPORTED)

    def test_dump_rotation(self):
        self.assertEqual(hl.dump_rotation(dump(rotation=3)), 3)
        self.assertIsNone(hl.dump_rotation("<hierarchy />"))

    def test_adb_failure_is_a_tool_error(self):
        def boom(*a, **k):
            raise hl.ToolError("adb: no devices")
        real = hl.adb
        hl.adb = boom
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(hl.main([]), hl.EXIT_ERROR)
            self.assertIn("no devices", err.getvalue())
        finally:
            hl.adb = real


if __name__ == "__main__":
    unittest.main()