# DuoProbe: dev-only feasibility probe (milestone M1)

A minimal, dependency-free app for the on-device dual-panel feasibility gates (G1 launch/reuse, G2 Home, G4 Back,
G5 IME, G6 keyguard, G7 posture/rotation). It is an AOSP module (`Android.bp`, public SDK only, no Gradle).

**Status: unbuilt.** It was written without an AOSP tree and has only been checked with a Java parser. Expect to fix
small compile errors on the first `m DuoProbe`.

## What it contains

| Component | Purpose |
|---|---|
| `ProbeHomeActivity` | A HOME-category activity (so the framework treats launches from it as launches from home). Two large launch areas, one per panel (laid out around the hinge band; A = left/top, B = right/bottom), each listing the probe activities and up to 8 launchable apps. Every tap, including the probe's own test activities, starts an `ACTION_MAIN` + `CATEGORY_LAUNCHER` intent with an explicit component (`ProbeIntents`; the panel routing in frameworks_base 0008 `panelForLaunch` ignores anything else) with `FLAG_ACTIVITY_NEW_TASK \| FLAG_ACTIVITY_RESET_TASK_IF_NEEDED`, `Intent.setSourceBounds(<on-screen rect of the tapped button>)`, `ActivityOptions.makeBasic()` and **no launch display id and no windowing mode**, which is what a fixed Launcher3 should send. A non-LAUNCHER intent is refused and logged as `launchFailed reason=notLauncherIntent`. The *Send launchDisplayId=0* toggle adds `setLaunchDisplayId(0)` to reproduce the Quickstep bypass. Back on the probe home is consumed and logged. |
| `ProbeTestActivity` | An ordinary app window (no `configChanges`, so it is relaunched like a normal app): two editors (IME gate, switching editor focus), *Show/Hide IME*, a requested-orientation cycle button (unspecified, portrait, landscape), *Launch another test task adjacent* (`FLAG_ACTIVITY_LAUNCH_ADJACENT \| NEW_TASK \| MULTIPLE_TASK`), *Log state now*, *Finish*. |
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

### Option 2: include it in a dev system image (PRODUCT_PACKAGES hook)

Only in the dev tree's generated product makefile (`device/phh/treble/aosp.mk`, copied from `build/aosp.mk` by
`applyPatches` in `build.sh`), append after the patches are applied:

```make
# DEV ONLY - never commit to build/aosp.mk
PRODUCT_PACKAGES += DuoProbe
```

then build the image as usual. Remove the line (or start from a clean dev tree) before any other build.

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
