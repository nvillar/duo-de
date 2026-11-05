# duo-gesture: host tests and replay for the three-finger gestures

Host-side tooling for the logging-only three-finger gesture recognizer
(frameworks_base patch `0020-Duo-three-finger-gesture-recognizer-logging-only.patch`). What the gestures are,
how to enable them on a device, the log format and the tuning procedure are in
[docs/three-finger-gestures.md](../../docs/three-finger-gestures.md).

| Path | Content |
|---|---|
| `duo-gesture.sh` | Runs the tests, or replays captures. Takes the classifier, `DuoPanelGeometry` and the JUnit tests out of the patch files (`git apply` into a temporary directory), so it tests exactly what `dev/build.sh` applies |
| `src/org/duode/gesture/GeteventReplay.java` | Parses `getevent -lt` multi-touch captures and feeds them to the classifier the way InputReader builds MotionEvents (raw → display scaling, rotation, lowest-free pointer ids, ups → move → downs per frame) |
| `tests/org/duode/gesture/ReplayFixtureTest.java` | Expected reports for every touch of the captures, rotation invariance, parser checks |
| `testdata/` | Raw Duo 1 captures, described in [testdata/README.md](testdata/README.md) |

The unit tests on synthetic touch streams (`DuoThreeFingerClassifierTest`, `TouchScript`) live in the patch, under
`packages/SystemUI/tests/DuoGestureTests` (`atest --host DuoThreeFingerClassifierTests` in an Android tree).

## Running

On Linux or WSL (the `duo-build` distribution has everything under `~/aosp`; nothing in it is modified):

```bash
tools/duo-gesture/duo-gesture.sh test
tools/duo-gesture/duo-gesture.sh replay --all tools/duo-gesture/testdata/touch3.getevent
tools/duo-gesture/duo-gesture.sh replay --config pinchDp=30,swipeDp=60 --rotation 1 capture.getevent
```

Java 17 or later and JUnit 4 come from `$JAVA_HOME`/`PATH` and `$JUNIT_CP`, or else from the Android tree
(`--aosp DIR`, default `$AOSP` or `~/aosp`): `prebuilts/jdk/jdk21` and
`prebuilts/devtools/tools/lib/{junit-4.12,hamcrest-core-1.3}.jar`. `--fwb DIR` takes the sources from a
frameworks/base checkout instead of the patches, for iterating on the classifier before regenerating the patch.

`replay` prints one line per result in the on-device log format, prefixed with the touch number, its start time
in the capture and its contact count; `--all` also lists touches that reported nothing. `--rotation` rotates the
captured (natural orientation) coordinates as the display would; `--device /dev/input/eventN` keeps one device of
a capture taken from all devices (`getevent -lt` without a device).

After a tuning session, add useful captures to `testdata/` with their expected reports in
`ReplayFixtureTest`.
