# Three-finger app gestures: logging-only prototype

Status: **implemented, not built or run on a device yet** (frameworks_base patch
`0020-Duo-three-finger-gesture-recognizer-logging-only.patch`). It recognises the gestures and logs them; it
takes **no action**. Span, contract and push become actions in M3, on top of the M2.1 topology controller and the
M2.5 move/swap/span commands.

Related: [dual-screen plan](dual-screen-plan.md), [host tests and replay](../tools/duo-gesture/README.md),
[development setup](development-setup.md).

## Gestures

Owner decisions (plan, three-finger gesture decision and "own apps are the primary workload"):

| Gesture | Logged as | Meaning (M3) |
|---|---|---|
| Pinch-out, three fingers on one panel | `gesture=SPAN target=<panel>` | Span the app on that panel |
| Pinch-in, three fingers on one panel | `gesture=CONTRACT target=<panel>` | Contract onto that panel |
| Pinch with fingers starting on both panels | out: `SPAN`, in: `CONTRACT`; `target` = the panel with two of the three fingers | As above |
| Sideways swipe, three fingers on one panel, toward the other panel | `gesture=PUSH target=<from> to=<other>` | Push the app over (swap or move, decided in M2.5/M3) |

No TalkBack or per-app exclusions; one global on/off setting.

## Recognition rules

- Fingers are classified by the panel they **land** on and measured from where they landed. They may start moving
  while the others land (up to 300 ms after the first); later movement before the third finger makes the touch
  something else (`moved-before-three`).
- The decision is taken **once**, when the spread change reaches 40 dp (`pinchDp`) or the translation 80 dp
  (`swipeDp`):
  - on one panel, the winner's score (distance / threshold) must be at least twice the other's, otherwise
    `ambiguous`;
  - across the panels, a pinch must be at least half the translation across the hinge (a thumb that stays on one
    panel is fine); translation without a pinch is `swipe-split` (pushes start on one panel only);
  - every finger must agree: no pinch finger may move against the pinch by more than half of it, every push
    finger must cover 30 % of the push (`fingers-disagree`);
  - a push must be across the hinge, not along it (`not-sideways`), and toward the other panel
    (`away-from-other-panel`). "Across the hinge" follows the rotation.
- A recognised gesture is followed until the first of the three fingers lifts (or slides onto the display
  boundary): `COMMIT` if it still has half its threshold in the recognised direction, otherwise
  `CANCEL reason=reversed`.
- Ineligible touches: a finger landing in an edge-swipe zone (`edge-start`: outer and hinge-side edges 24 dp,
  top 32 dp, bottom 48 dp), a fourth finger (`fourth-finger`), a stylus (`non-finger`), the keyguard showing at the
  first finger down (`keyguard`), only one panel on the display (`layout-single`).
- Ignored contacts: anything landing on the hinge band or within 3 dp of the display boundary (controller artefacts,
  fingers parked after sliding off an edge), and brief contacts near the hinge while three fingers are down.
- Touches that never had three counted fingers report nothing.

Other reasons: `no-decision` (three fingers lifted without moving, such as a three-finger tap),
`lifted-before-decision`, `input-cancelled` (the touch was cancelled, for example when another gesture pilfered
it), `pointer-cancelled` (a pointer up with `FLAG_CANCELED`, such as palm rejection: the pointer was cancelled,
not lifted, so it never commits a gesture).

## Enabling it

Needs an image with patch 0020, two-panel mode on (`persist.debug.phh.duo.panels=1`, see
[development setup §7](development-setup.md#experimental-two-panel-mode)), and:

```powershell
adb shell getprop sys.duode.panels.active                        # must be 1
adb shell settings put global duode_three_finger_gestures 1      # on, immediately; 0 or delete to turn off
adb logcat -v time -s DuoGesture:I
```

At boot SystemUI logs `ready on duo: panelsActive=... enabled=...`; switching on logs
`enabled (logging only, no actions) config ...`. It never pilfers pointers, so apps keep receiving the same
touches (a three-finger pinch in a browser still zooms it while the log says `SPAN`).

Threshold overrides apply from the next touch, no restart (keys and defaults below):

```powershell
adb shell settings put global duode_three_finger_gestures_config "pinchDp=30,swipeDp=60"
adb shell settings delete global duode_three_finger_gestures_config
```

State and the last 20 results:

```powershell
adb shell dumpsys activity service com.android.systemui/.SystemUIService com.android.systemui.duo.gesture.DuoThreeFingerGestureMonitor
```

## Log format

One line per result, tag `DuoGesture`; a recognised gesture gives a `RECOGNIZED` line (where M3 would take over
the touch) and a `COMMIT` or `CANCEL` line at the end:

```
event=RECOGNIZED gesture=SPAN motion=pinch-out split=3+0 target=0 to=-1 pinchPx=+108 scale=1.60 acrossPx=+9 alongPx=-20 ms=1040 rot=0 reason=pinch
event=COMMIT gesture=SPAN motion=pinch-out split=3+0 target=0 to=-1 pinchPx=+296 scale=2.66 acrossPx=-14 alongPx=-69 ms=1339 rot=0 reason=parked
```

| Field | Meaning |
|---|---|
| `event` | `RECOGNIZED`, `COMMIT` or `CANCEL` |
| `gesture`, `motion` | `SPAN`/`CONTRACT`/`PUSH`/`NONE`; `pinch-out`/`pinch-in`/`swipe`/`none` |
| `split` | fingers that landed on panel 0 + panel 1 |
| `target`, `to` | panel the command is for (push: from), push destination; -1 if none |
| `pinchPx`, `scale` | spread change in px and ratio (one panel: mean distance from the centroid; across: half / ratio of the separation between the two panels' finger groups) |
| `acrossPx`, `alongPx` | translation across the hinge (+ = toward panel 1) and along it |
| `ms` | since the third finger landed (since the first when three were never tracked) |
| `rot` | display rotation |
| `reason` | why: `pinch`/`swipe` at recognition, `lift`/`parked` at commit, or the cancel reason |

Panel 0 is the left panel at rotation 0 and follows the physical panel when the device turns
(`DuoPanelGeometry`).

## Thresholds

`duode_three_finger_gestures_config` takes `key=value` pairs separated by commas; distances are dp
(2.5 px/dp on the Duo).

| Key | Default | Meaning |
|---|---|---|
| `pinchDp` | 40 | spread change that decides a pinch |
| `swipeDp` | 80 | translation that decides a push |
| `dominance` | 2 | one panel: winning score / other score |
| `crossDominance` | 0.5 | across: pinch / translation across the hinge |
| `sideways` | 1 | push: across / along |
| `pinchAgainst` | 0.5 | how far one pinch finger may move against the pinch, as a fraction of it |
| `swipeFollow` | 0.3 | fraction of the push every finger must cover |
| `commitFraction` | 0.5 | fraction of the threshold still needed at the first lift |
| `slopDp`, `landingMs` | 12, 300 | movement before the third finger, tolerated during the first 300 ms |
| `edgeDp`, `hingeDp`, `topDp`, `bottomDp` | 24, 24, 32, 48 | edge-swipe zones (outer edges, hinge-side edges, top, bottom) |
| `boundaryDp` | 3 | contacts this close to the display boundary are ignored or count as lifted |
| `density` | display density | px per dp |

The defaults recognise every intended gesture in the two captures from 2026-11-04 and reject their other touches
(`tools/duo-gesture/testdata/README.md`); they have not been tried against everyday use.

## On-device tuning procedure

For the coordinator with the device owner, on an image with patch 0020 and two-panel mode on. About 20 minutes.

1. Enable and check: `sys.duode.panels.active` is 1, the setting is 1, `dumpsys` shows `monitor=true`.
2. In one terminal, follow the log: `adb logcat -v time -s DuoGesture:I | Tee-Object gesture-log.txt`.
3. In another, record the raw touches for offline replay (on the device, so PowerShell does not re-encode them;
   the touchscreen is the `surface_touchscreen` device, `event4` on the gate image):

   ```powershell
   adb shell getevent -pl | Select-String -Context 2,0 surface_touchscreen   # the "add device N: /dev/input/eventN" line
   adb shell "getevent -lt /dev/input/event4 > /data/local/tmp/gestures.getevent"
   # ... perform the trials, then Ctrl+C
   adb pull /data/local/tmp/gestures.getevent
   ```

4. Trials, announced one by one so the log can be matched (5 repetitions each, with an app on each panel; vary
   speed and finger spacing):
   - positives: pinch-out and pinch-in on panel 0 and on panel 1; across the hinge 2+1 and 1+2, out and in;
     push 0 → 1 and 1 → 0; one push and one pinch reversed before lifting (expect `CANCEL reversed`);
   - negatives (expect no `RECOGNIZED`): two-finger zoom and scroll in an app on each panel; three-finger tap;
     three-finger swipe up/down; fast typing on the keyboard; Back swipes from the outer and hinge-side edges;
     notification shade pull; swipe-up Home; holding the device by both panels; three-finger pinch with the
     keyguard showing (expect `CANCEL keyguard`);
   - once in rotation 90 (panels stacked): one pinch per panel and one push each way.
5. Count misses (no `RECOGNIZED` for a positive: note the `CANCEL` reason and metrics) and false positives
   (`RECOGNIZED` during a negative). False positives matter most: they become unwanted window changes in M3.
6. Tune offline, on the same touches: `tools/duo-gesture/duo-gesture.sh replay --config pinchDp=30
   gestures.getevent`, and check the new values on the device through `duode_three_finger_gestures_config`.
7. Keep the capture: add it to `tools/duo-gesture/testdata/` with the trial list and its expected reports in
   `ReplayFixtureTest`, and change the defaults in the classifier if the owner agrees.
8. Turn it off: `adb shell settings put global duode_three_finger_gestures 0`; `adb shell rm
   /data/local/tmp/gestures.getevent`.

## Implementation

- `packages/SystemUI/src/com/android/systemui/duo/gesture/DuoThreeFingerClassifier.java`: pure Java on top of
  `DuoPanelGeometry` (fw 0007), no Android dependencies, no allocation per move event.
- `DuoThreeFingerGestureMonitor`: a `CoreStartable` (bound by `DuoGestureModule` in `ReferenceSystemUIModule`)
  that registers a gesture input monitor (`InputMonitorCompat`, as `EdgeBackGestureHandler` does) on the default
  display, on the main thread, only while enabled; reads the display size and rotation and the keyguard state at
  each first finger down.
- Tests: `DuoThreeFingerClassifierTests` (host, in the patch) and the replay tests in `tools/duo-gesture`.

## Open risks

- Not built: the SystemUI classes were type-checked against the r4 `SystemUI-core` and framework header jars of an
  earlier build, without Dagger processing; `Android.bp` changes (the classifier filegroup and the host test
  module) were not evaluated by Soong.
- The gesture monitor and Launcher3's TouchInteractionService both watch every touch. When Quickstep or the Back
  gesture pilfers pointers, the monitor gets `ACTION_CANCEL` (logged as `input-cancelled`); this is the expected
  behaviour, but it means the log can under-report gestures that start near system gesture areas.
- Writing an unknown global setting from `adb shell settings put global` is expected to work on Android 16 for
  the shell; if it does not, the setting needs a declared key or the switch moves to a debug property.
- Thresholds come from two short captures by one user; everyday false positives are unmeasured.
- Actions are not designed here. In M3 the recognizer pilfers pointers at `RECOGNIZED`, which cancels the app's
  touch; the reversal-to-cancel behaviour and the edge-zone exclusions must then be re-checked with live preview.
