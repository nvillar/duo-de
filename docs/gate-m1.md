# M1 gate runbook (Surface Duo 1, two panel prototype)

Operator steps for the M1 architecture-feasibility gate (G1-G9) of the dual-screen plan. Disposable prototype: the
image is a dev build, not a release. Device flashing, PIN changes and recovery stay operator decisions.

**Scripted collector (M2.0).** `tools/duo-gate` runs these gates over adb with unique log markers, real input receipt
(touch/text sentinels), fail-closed PASS / FAIL / INCOMPLETE verdicts per check and an evidence directory; it guides the
manual steps (folds, Recents, hinge-edge and predictive Back) without ever marking them passed. Use it for gate evidence;
the manual commands below remain the reference for what it checks. See [`tools/duo-gate/README.md`](../tools/duo-gate/README.md):

```sh
uv run --no-project python tools/duo-gate/duo_gate.py preflight
uv run --no-project python tools/duo-gate/duo_gate.py run            # G1 G2 G4 G5 G6 G9
uv run --no-project python tools/duo-gate/duo_gate.py run all --allow-reboot
```

It needs DuoProbe `gate-2` (touch/text sentinel and `targets` events, *Probe test 2 (own task)*).

## 0. What the image contains

Harness branch `m1-gate-prototype` on top of the reproducible dev build harness:

| Change | Purpose |
|---|---|
| frameworks_base 0007/0008 | panel geometry; two panel window policy (opt-in) |
| frameworks_base 0009/0010 | setup wizard on the first panel |
| frameworks_base 0011 | launcher icon routed to its panel even with `launchDisplayId=0` (finding P1a) |
| frameworks_base 0012/0013 | IME container takes the target task's panel bounds |
| frameworks_base 0014 | Home-minimized tasks use their own hidden flag, restored when the task is moved to front again (finding P1b) |
| frameworks_base 0015 | IME confinement re-evaluated on rotation/fold with an unchanged IME target |
| frameworks_base 0016 | publishes `sys.duode.panels.active` (two panel hierarchy built this boot) |
| Launcher3 0007 | refresh display size when setup ends |
| Launcher3 0008 | fullscreen launches (no freeform, no launch bounds) while `sys.duode.panels.active=1` |
| device_phh_treble 0015 | PaneManager geometry fix |
| DuoProbe (dev only) | probe HOME + test activities, logcat tag `DuoProbe` (`tools/duo-probe/README.md`) |

## 1. Build and identify the image

On the build host, with the harness checked out at `m1-gate-prototype` and clean:

```bash
~/duo-de/dev/podman.sh shell ~/aosp
~/duo-de/dev/check-apply.sh --unshallow ~/aosp      # inside the container; whole series must apply
exit
~/duo-de/dev/podman.sh build ~/aosp --with-probe --label m1gate
```

`provenance.txt` must show `duo_probe: included ...`. Flash with the existing dev-image procedure
(`docs/development-setup.md`). On the device:

```bash
adb shell getprop ro.duode.dev_build          # true
adb shell getprop ro.duode.dev_probe          # true
adb shell pm path org.duode.probe             # /system/app/DuoProbe/DuoProbe.apk
adb shell getprop ro.system.build.fingerprint # contains the build id
```

## 2. Modes, HOME and evidence

Panel mode (reboot after each change; the framework reads the properties once at boot):

```bash
adb shell getprop persist.sys.phh.duo.panels; adb shell getprop persist.debug.phh.duo.panels
adb shell setprop persist.debug.phh.duo.panels 1 && adb reboot     # panel mode on
adb shell setprop persist.debug.phh.duo.panels 0 && adb reboot     # panel-off control (also clear .sys. if set)
adb shell dumpsys window containers | grep -E 'Panel[01](Tasks)? |DefaultTaskDisplayArea|ImeContainer'
```

Expected on: `Panel0Tasks ... bounds=[0,0][1350,1800]`, `Panel1Tasks ... bounds=[1434,0][2784,1800]`. Off: no `Panel*`.

HOME selection (record the original first; it is Quickstep on the baseline):

```bash
adb shell cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME
adb shell cmd package set-home-activity org.duode.probe/.ProbeHomeActivity          # probe HOME
adb shell cmd package set-home-activity <recorded Quickstep component>               # back to Launcher3
```

Probe HOME: area A = panel 0 (left), area B = panel 1 (right). Buttons: *Probe test*, *Probe test 2 (own task)*,
*Probe test (new instance)*, *Probe locked (showWhenLocked)*, up to 8 apps; checkbox *Send launchDisplayId=0* reproduces
the Quickstep option.

**Two probe tasks.** *Probe test* always reuses its one task (NEW_TASK without MULTIPLE_TASK; the reusable-task
lookup searches every task area), so tapping it on B moves the task from A. Whenever a step needs a probe on each
panel, use *Probe test* on A and *Probe test 2 (own task)* on B (its own task affinity, so it is reusable on B too; the
older *Probe test (new instance)*, NEW_DOCUMENT | MULTIPLE_TASK, always creates another task), and check that the two
`task=` ids differ (`onResume` lines, or `activities.txt`) before continuing.

The image also publishes `adb shell getprop sys.duode.panels.active` (1 when the two panel hierarchy was built this
boot; Launcher3 uses it to stop requesting freeform).

Evidence per gate (`G=g1` etc.); keep logcat running for the whole gate:

```bash
mkdir -p m1/$G && adb logcat -c
adb logcat -v threadtime -s DuoProbe DuoDisplayAreaPolicy AndroidRuntime > m1/$G/logcat.txt &
# ... run the steps ...
snap() {  # snap <name>
  adb shell dumpsys window containers  > m1/$G/$1-containers.txt
  adb shell dumpsys activity activities > m1/$G/$1-activities.txt
  adb shell dumpsys window windows      > m1/$G/$1-windows.txt
  adb shell dumpsys input               > m1/$G/$1-input.txt
  adb exec-out screencap -p             > m1/$G/$1.png
}
```

`DuoProbe` lines are `event=<name> act=<Class@id> task=<id> ... bounds=... multiWindow=...` (README, Log format);
gate-2 adds `touch` (raw coordinates of each touch received), `text` (gate sentinel tokens found in an editor, never the
text) and `targets` (on-screen rectangles of the probe's controls).
`DuoDisplayAreaPolicy` logs `Home: minimizing PanelN (k tasks)`, `Restored minimized Task{...}`, `IME bounds ...`.
A task hidden by Home shows `mForceHiddenFlags=8` in `activities.txt` (8 = the policy's own flag; 2 = Shell's task
organizer; 1 = PiP). Touch and fold outcomes must be observed on the device; screenshots alone do not prove them.

## 3. Gates

Panel bounds below are for the unrotated, unfolded Duo 1: P0 = `[0,0][1350,1800]`, P1 = `[1434,0][2784,1800]`.

### G1 Launch/reuse

1. Probe HOME, toggle off. Side A: tap *Probe test* (launch), press Home on A, tap again (reuse); repeat to 10
   launches/reuses. Tap *Finish* in the probe test to end its task (do not force-stop the package: that also kills the
   probe HOME), then do the same on side B. Then tick *Send launchDisplayId=0* and repeat 10 per side.
   `snap` once per block.
2. Cross-panel reuse: *Probe test* on A, Home on A, then tap *Probe test* on B, 5 times alternating. Expected: the same
   `task=` id moves to the tapped panel (contract: a tap on a panel opens/reuses the app there). Record the result.
3. Launcher3 path: Quickstep HOME; tap Calendar (or Settings) from a workspace/hotseat icon on the left, then the right,
   10 times each (Home between taps).
4. Negative controls: `adb shell am start --display 0 -n org.duode.probe/.ProbeTestActivity` (not a home launch; must
   keep the platform choice, the default area); panel-off reboot and repeat steps 1 and 3 once per side.

Pass: every `launch side=A` is followed by `onResume` of a `ProbeTestActivity` whose `bounds` equal P0 (B: P1), with
the task under `Panel0Tasks` / `Panel1Tasks` in `containers.txt`, `mode=fullscreen`, never `freeform`; reuse on the
same side keeps the same `task=` id. Launcher3 launches land in the tapped panel, fullscreen in it (Launcher3 0008 +
frameworks 0011).
Panel-off: tasks in `DefaultTaskDisplayArea`; Launcher3 behaves as on the baseline (freeform preference unchanged).
Record requested windowing mode (`launch ... windowingMode=`; for Launcher3 `activities.txt`) and final bounds.

### G1b P1b regression (Home, fold, relaunch)

Probe test on B; Home on B (`Home: minimizing Panel1`; `mForceHiddenFlags=8`). Fold so only one panel is lit; relaunch
the same app from HOME. Repeat with Calendar from Launcher3. Pass: the app is visible and resumed; `Restored minimized`
is logged; no task keeps `mForceHiddenFlags=8` after it is relaunched; no `mForceHiddenFlags=2` is cleared by the policy.

### G2 Independent Home

Probe test on A and *Probe test 2 (own task)* on B (two distinct `task=` ids). Focus B (tap its editor), Home
gesture on B. Repeat focusing A, Home on A. 10 each.

Pass: only the focused panel's tasks are minimized (log names the right panel); the other panel's probe stays visible
and takes input (tap its editor: `editorFocus` from that `act=`); no `onStop` for it. Record whether it stays `RESUMED`
(`onTopResumedActivityChanged` may move; `onPause` without `onStop` is acceptable, record it).

### G3 Recents / All Apps

Quickstep HOME, two different apps, one per panel (Calendar left, Settings right; two distinct task ids). Open
Overview from each panel, select a task, then open and cancel. Open All Apps
on each panel. Repeat once with the probe HOME (Quickstep fallback Recents).

Pass: Overview and All Apps are visible and interactive; select returns the task to the invoking panel; cancel returns
both tasks; the other task keeps its `task=` id and is not finished. A global (whole display) Overview is a provisional
prototype result only; record it as such.

### G4 Back / focus

Probe test on A and *Probe test 2 (own task)* on B (two distinct `task=` ids). Focus B, then Back gesture from A's
outer edge (x≈0). Repeat mirrored (from x≈2783), then from
the hinge-side edges (x≈1349 on A, x≈1434 on B). Start a Back swipe and cancel it once per side.

Pass: Back targets the panel the gesture started on (`onDestroy` / finish of that side's `act=` only), even when the
other was focused; cancel produces no lifecycle change; predictive animation stays within that panel (record with
`adb shell screenrecord`). Record hinge-edge behaviour as observed.

### G5 IME

Probe test on A and *Probe test 2 (own task)* on B (two distinct `task=` ids). Tap A's first editor, then A's second
editor, then B's editor; rotate with the keyboard open, and fold to one panel with the keyboard open (the probe is
relaunched on configuration changes; repeat with an installed app that declares `configChanges` for orientation and
screen size, so its window survives, to exercise frameworks 0015).

Pass: `ImeContainer requested-bounds` in `containers.txt` equals the target panel (frameworks 0012); the target probe
logs `event=insets ... imeVisible=true` and the other probe does not get IME insets; taps on the other panel reach it while the
keyboard is up; keys type into the focused editor; after rotation the keyboard is still within the target panel.

### G6 Authentication safety (stop the gate on failure)

Needs a screen lock (operator's choice). *Probe locked (showWhenLocked)* on A (its own task), Probe test on B; check
the two `task=` ids differ. Lock
(`adb shell input keyevent 26`), wake (`adb shell input keyevent 224`), `snap`, unlock. 10 cycles, then swap sides.

Pass: keyguard covers both panels on every cycle; no ordinary (non-showWhenLocked) content of B is visible or touchable
(no `DuoProbe` events from B's `act=` while locked; B's window not on screen in `windows.txt`). Any leak is a safety
stop, not an alpha limitation.

### G7 Physical posture

Probe test on A and *Probe test 2 (own task)* on B, plus Calendar from Launcher3 for the Launcher3 path (distinct
task ids). Fold toward each side 10 times, unfold; rotate through 0/90/180/270 once in each posture.
`adb shell dumpsys device_state | grep mCommittedState` and `snap` after each change.

Pass: no task lost (same `task=` ids in `activities.txt`), no stranded `mForceHiddenFlags=8` (only tasks the user sent
Home on, and those come back on relaunch), no touch delivered to the dark panel's probe, panel bounds follow the
posture. Compare with the proposed fold policy (plan section 3) and record differences.

### G8 Transitions

During G1-G4, record `adb shell screenrecord --time-limit 30 /sdcard/g8.mp4` and `snap` mid-transition where possible.
Optionally capture a Winscope (WM + transitions) trace; the exact r4 trace commands are not verified here.

Pass: app surfaces and input regions (`windows.txt` touchable regions, `input.txt` frames) stay inside their panel
except an intentional span; no flicker of the other panel. Note home/wallpaper redraw with both panels occupied
(`adb shell dumpsys gfxinfo <home package>` frame count over an idle minute).

### G9 Stability / escape

Throughout: `adb logcat -b crash -d > m1/g9/crash.txt`; `adb shell dumpsys dropbox --print system_server_crash` and
`system_app_crash`, `SYSTEM_TOMBSTONE`. Then set both panel properties to 0, reboot, restore the recorded HOME, and
re-run G1 step 1 once.

Pass: no attributable `system_server` / SystemUI crash during the smoke set; panel-off operation is restored (no
`Panel*` areas, apps fullscreen across the display as on the baseline, Launcher3 as on the baseline).

## 4. Report

One row per gate: result (pass / fail / provisional), run count, evidence directory, deviations. Apply the plan's
pivot criteria (section 6): G6 failure stops; routing/geometry/hidden-state failures call for local fixes; Recents,
Back or IME needing scattered per-display changes triggers the candidate B comparison.
