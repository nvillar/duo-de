# DuoProbe: dev-only feasibility probe (milestone M1)

A minimal, dependency-free app for the on-device dual-panel feasibility gates (G1 launch/reuse, G2 Home, G4 Back,
G5 IME, G6 keyguard, G7 posture/rotation). It is an AOSP module (`Android.bp`, public SDK only, no Gradle).

**Status:** built in an android-16.0.0_r4 tree through `dev/build.sh --with-probe`; it was
included in the M1 gate image (tag `m1-gate-image`) and used on a Duo 1 for the M1 gates
([report](../../docs/m1-gate-report.md)). The `gate-2` additions for `tools/duo-gate` (touch/text sentinel and
`targets` events, `ProbeOtherTestActivity`) were built into the `m22recents` image and used for the first collector
run. `gate-3` (`targets` also after a scroll, one `OnScrollChangedListener`) is not built yet.

## What it contains

| Component | Purpose |
|---|---|
| `ProbeHomeActivity` | A HOME-category activity (so the framework treats launches from it as launches from home). Two large launch areas, one per panel (laid out around the hinge band; A = left/top, B = right/bottom), each listing the probe activities and up to 8 launchable apps. Every tap, including the probe's own test activities, starts an `ACTION_MAIN` + `CATEGORY_LAUNCHER` intent with an explicit component (`ProbeIntents`; the panel routing in frameworks_base 0008 `panelForLaunch` ignores anything else) with `FLAG_ACTIVITY_NEW_TASK \| FLAG_ACTIVITY_RESET_TASK_IF_NEEDED`, `Intent.setSourceBounds(<on-screen rect of the tapped button>)`, `ActivityOptions.makeBasic()` and **no launch display id and no windowing mode**, which is what a fixed Launcher3 should send. A non-LAUNCHER intent is refused and logged as `launchFailed reason=notLauncherIntent`. The *Send launchDisplayId=0* toggle adds `setLaunchDisplayId(0)` to reproduce the Quickstep bypass. Back on the probe home is consumed and logged. |
| `ProbeTestActivity` | An ordinary app window (no `configChanges`, so it is relaunched like a normal app): two editors (IME gate, switching editor focus), *Show/Hide IME*, a requested-orientation cycle button (unspecified, portrait, landscape), *Launch another test task adjacent* (`FLAG_ACTIVITY_LAUNCH_ADJACENT \| NEW_TASK \| MULTIPLE_TASK`), *Log state now*, *Finish*. |
| `ProbeOtherTestActivity` | The same window in a second reusable task (own `taskAffinity`), started by *Probe test 2 (own task)*: one reusable probe task per panel (*Probe test* on A, *Probe test 2* on B), each minimized and relaunched independently. |
| `ProbeLockedTestActivity` | The same window with `android:showWhenLocked="true"`, in its own task (keyguard gate). |
| `ProbeActivity` / `ProbeLog` / `ExtensionsProbe` | Logging shared by all three. |

## Log format

Everything is logged with the tag `DuoProbe`, one line per event:
`event=<name> act=<Class@id> task=<taskId> key=value ...`

Events: `onCreate` (with `relaunched`, intent flags, `sourceBounds`), `onNewIntent`, `onStart`, `onRestart`,
`onResume`, `onTopResumedActivityChanged`, `onPause`, `onStop`, `onDestroy`, `onUserLeaveHint`,
`onWindowFocusChanged`, `onConfigurationChanged`, `onMultiWindowModeChanged`, `layout` (root rect on screen, when it
changes), `insets` (IME visibility and insets, system bars, cutout, when they change), `editorFocus`, `imeRequest`,
`requestOrientation`, `launch` / `launchFailed` (side, component, flags, source bounds, launch display id),
`toggle`, `backInvokedOnHome`, `config*` (full `Configuration`, which includes the window configuration: bounds,
app bounds, windowing mode), `displayMask` (hinge rects from the platform `com.microsoft.device.display.DisplayMask`,
read reflectively) and `windowExtensions` / `windowLayoutInfo` (display features from the platform
`androidx.window.extensions` library, read reflectively; logs `absent` when the library is not on the device).
Lifecycle lines also carry `display`, `rotation`, `bounds` (current window metrics), `maxBounds`, `multiWindow`,
`orientation`, `sizeDp`, `swDp`, `dpi`, `requestedOrientation` and `keyguardLocked`.

Gate-collector events (`gate-2`, used by `tools/duo-gate` as proof of real input receipt; `gate-3` adds `targets` after a
scroll, so the collector can scroll a control covered by the keyboard into view):

| Event | Keys | Meaning |
|---|---|---|
| `touch` | `action=down\|up\|cancel x y rawX rawY pointers` | a touch the window received (raw = display coordinates); moves are not logged |
| `text` | `editor=1\|2 len sentinels=<tokens>\|none` | the set of gate sentinels (`dgs` + 8 hex) found in an editor changed; **the editor text itself is never logged** |
| `targets` | `<name>=[l,t][r,b] ...` | on-screen rectangles of the named controls after a layout change (gate-3: also after a scroll): home `A.toggle`, `A.probeTest`, `A.probeTest2`, `A.probeTestNew`, `A.probeLocked` (and `B.*`); test window `title`, `editor1`, `editor2`, `showIme`, `hideIme` |

```sh
adb logcat -s DuoProbe
```

## Keeping it out of release builds

The harness checkout lives inside the Android source root (Docker: `/aosp/treble_aosp`; `build.sh`: `<root>/duo-de`),
and Soong's finder scans the whole tree for `Android.bp`. `tools/duo-probe/.find-ignore` makes the finder prune this
directory (android-16.0.0_r4 `build/soong/ui/build/finder.go`, `PruneFiles: .out-dir, .find-ignore`; symlinks are not
followed unless `ALLOW_BP_UNDER_SYMLINKS=true`), so release builds never see the module. Do not remove that file and
do not add `DuoProbe` to `build/aosp.mk` or any makefile in this repository.

## Building it in a disposable dev tree

From the Android source root of a **dev** checkout (never a release or shared tree), after the patches are applied:

```sh
# copy (not symlink) the module somewhere Soong scans; the copy has no .find-ignore
rm -rf packages/apps/DuoProbe
cp -r <harness>/tools/duo-probe/DuoProbe packages/apps/DuoProbe

source build/envsetup.sh
lunch treble_arm64_bvN-bp2a-userdebug
m DuoProbe
```

### Option 1 (preferred): install over adb, no reflash

The app is unprivileged and test-key signed, so it installs on the released image as an ordinary app:

```sh
adb install -r "$OUT/system/app/DuoProbe/DuoProbe.apk"
```

### Option 2: include it in a dev system image (`dev/build.sh --with-probe`)

The developer build harness does this for you, only in the dev tree and only for that build:

```sh
dev/podman.sh build ~/aosp --with-probe --label m1gate
```

It copies `DuoProbe` to `packages/apps/DuoProbe` and adds `PRODUCT_PACKAGES += DuoProbe` to the untracked, generated
`device/phh/treble/duo-dev.mk`; `provenance.txt` records it (`duo_probe:`). The next build without `--with-probe`
removes both; run that build without `--no-installclean`, otherwise the APK left in `out/` stops it. Never add the module to `build/aosp.mk` or any tracked makefile.

## Using it on the device

```sh
# record the current HOME so it can be restored afterwards
adb shell cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME

# make the probe the HOME activity
adb shell cmd package set-home-activity org.duode.probe/.ProbeHomeActivity

# ... run the gates, collect `adb logcat -s DuoProbe` plus dumpsys activity/window ...

# restore the HOME recorded above, then remove the probe
adb shell cmd package set-home-activity <recorded component>
adb uninstall org.duode.probe
```

The test activities have no LAUNCHER intent filter, so they do not appear in other launchers (the probe starts them
with explicit ACTION_MAIN + CATEGORY_LAUNCHER intents, which need no filter). To compare against the real
Launcher3/Quickstep launch path, launch an installed app such as Calendar from Launcher3, or start a probe activity
directly with `adb shell am start -n org.duode.probe/.ProbeTestActivity` (not a home launch, so not panel-routed).
