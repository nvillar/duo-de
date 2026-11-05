# M1 feasibility gate report — Surface Duo 1, vanilla, Android 16 r4

Date: 2026-11-04. Plan: [dual-screen-plan.md](dual-screen-plan.md) (section 6 M1 gates; sections 15-17 status and M2). Runbook: [gate-m1.md](gate-m1.md).

| Image | Harness commit | System image SHA-256 |
|---|---|---|
| `duodev.20261105003423.bfb0d8b7.m1gate` (test-keys, built with `--with-probe`) | `bfb0d8b`, tag `m1-gate-image` | `de16c2a2336d3dfdf44246c5cc976c5191a955f77c9578579ecf9cb59a9efda7` |
| `duodev.20261104183202.0b049383.baseline` (test-keys, unmodified series), built, flashed and smoke-tested first | `0b04938`, tag `m0-baseline-image` | `536378902dac260466d06a478759d74300e1b31efabdb345fb0e66932f262dd9` |

Panel mode: `persist.debug.phh.duo.panels=1`; WM published `sys.duode.panels.active=1`. Raw evidence (window/activity/input dumps, screenshots, UI dumps, logcat) is kept by the operator outside the repository, because it can contain personal content.

## Results

| Gate | Result | Evidence / notes |
|---|---|---|
| G1 Launch | **Pass** | Launcher3 (Quickstep) left icon → Panel0Tasks `[0,0][1350,1800]`; right icon → Panel1Tasks `[1434,0][2784,1800]`; fullscreen, not freeform. Probe launches likewise (task 20 left, task 21 right; apps see 540×720 dp). |
| G1b Home → relaunch | **Pass** | Minimized task restored on relaunch (`Restored minimized`); fold variant covered under G7. |
| G2 Independent Home | **Pass** | Both apps RESUMED simultaneously; Home on the focused right panel stopped only that app; left app stayed resumed and focused. |
| G3 Recents | **Fail (contract)** | Synthetic gestures cannot open Overview; physical swipe-and-hold opens a **whole-display Overview** (provisional). Selecting a task moved it **out of its panel into the spanned default area** (`startActivityFromRecents` reparents to DefaultTaskDisplayArea). Recents key shows the transient taskbar **centred across the hinge**. |
| G4 Back | **Pass** | Back from the left outer edge acted on the left panel while the right was focused (CROSS_TASK), and mirrored. Hinge-side edges not yet tested. |
| G5 Keyboard | **Pass with defects** | IME container follows the editing panel (`[0,0][1350,1800]` / `[1434,0][2784,1800]`); only the target app gets non-zero IME insets; other panel stays usable. Defects: (a) ~~soft keyboard suppressed by default because a Duo input device is treated as a hardware keyboard (`show_ime_with_hard_keyboard=0`)~~ **withdrawn**: no physical keyboard is present (`dumpsys input` lists only the Virtual device; configuration `nokeys`) and with the default `show_ime_with_hard_keyboard=0` the soft keyboard shows on the first tap; the observation was defect (b); (b) right→left focus change needs a second tap (left→right and same-panel changes work on the first tap); (c) non-target apps see `imeVisible=true` with zero inset. Rotation/fold with keyboard open not yet run. |
| G6 Lockscreen | **Safe; functional gap** | Keyguard covered both panels; only SystemUI on screen; no leak. A `showWhenLocked` activity in a panel did **not** occlude the keyguard (affects alarms, calls, lockscreen camera). |
| G7 Folding | **Fail (WM)** | (a) Posture/sensor: **rare, unreproduced anomaly.** The first fold of the gate session read 2–27° for ~20 s (peek-mode clock, both displays) then 4° → 304° within 0.2 s. The primary hall sensor recorded **no closed event** during that window, although it reports closed immediately when the device really is closed face-to-face (02:03:19, hinge 2°) — so the low readings were most likely wrong, not operator error. Five controlled folds afterwards (slow; 11 s squeeze at back-to-back; normal speed flat and upright; first fold immediately after a reboot) all read continuously 0–360° and switched correctly; the "ambiguous until first hall closure after boot" hypothesis was **not** supported. The second hall sensor reported no events. Single-screen engaged at ~231–292° folding back, dual returned at ~172–201° unfolding; upright, the service switched left-only → right-only at ~357° (it picks the visible side). (b) WM: in single-screen both panel groups collapse to `[0,0][1350,1800]`; the other panel's apps stay on screen underneath (not parked); **right-only posture shows the left panel's app** because WM has no active-physical-side input. No tasks lost; all apps received config changes. |
| G8 Transitions | Not measured | Needs screen recording; deferred. |
| G9 Stability | **Pass** | No app/system_server crashes or ANRs during the gate. |

## Decision

**Keep the per-panel DisplayAreaGroup architecture.** Launch routing, independent Home, Back and IME placement all work with contained changes; Back required none. The lockscreen is safe. The failures are policy gaps the plan anticipated (active side, parking, Recents placement), not evidence that the topology cannot work. No pivot criterion is met.

Recents remains the highest-risk area: making selection return to the invoking panel and scoping Overview per panel needs Quickstep/Shell work. Timebox it at the start of M2; if it requires scattered per-handler rewrites, run the candidate-B comparison from the plan (section 6, M1 pivot criteria).

## Coverage caveat

The gate decision rests on **one to a few repetitions per check**, not the runbook's ten per side. Not yet run: G1 ten-repetition blocks, the probe's `launchDisplayId=0` toggle and negative controls (`am start --display 0`, panel-off rerun); G2 ten repetitions; G3 with the probe HOME and with Microsoft Launcher; G4 hinge-side edges and cancelled Back; G5 rotate/fold with the keyboard open and a `configChanges` app; G6 ten lock cycles and side swap; G7 ten physical folds per side with rotation; G8 transition recording. The *keep* decision is well supported by the mechanisms observed; the pass results themselves need the full repetitions before M2 exit.

## Additional observations

- G5: the HOME activity in the spanned default area received non-zero IME insets (`ime=1008`) while the keyboard was confined to a panel. Home/spanned windows must not get panel IME insets.
- G6 setup: `wm dismiss-keyguard` works only because no secure lock is set; G6 must be rerun with a PIN.
- Touch stopped responding once on the gate image's predecessor (baseline+panel mode, 13:32, power menu `GlobalActionsDialogLite` focused, system otherwise alive); fixed by reboot; cause unknown. Watch for recurrence; capture `dumpsys input` before rebooting.
- Peek mode (`persist.sys.phh.duo.peek_mode_enabled=1`) shows a translucent clock overlay on both panels when the posture service decides the device is nearly closed.
- `sys.duode.panels.active=1` was published by the WM at boot (fw 0016) and Launcher3 launched fullscreen into panels.

## Device state left after the gate (test settings, not defaults)

`persist.debug.phh.duo.panels=1`; HOME = `org.duode.probe/.ProbeHomeActivity` (Quickstep still installed); `stay_on_while_plugged_in=7`; `show_ime_with_hard_keyboard=1`; WebView provider manually set to `com.android.webview`. All are lost or must be reset on a wipe.

## Must-fix list carried into M2

This list is the state at the end of the gate. The plan's findings ledger ([section 16.1](dual-screen-plan.md#161-ledger)) supersedes it after review; in particular, non-target apps reporting `imeVisible=true` with zero insets (G5 defect (c)) is documented `WindowInsets.isVisible()` behaviour, not a defect, and IME insets for home/spanned windows are to be audited by target and occlusion rather than suppressed wholesale (F10); and the hinge-jump filter in item 6 is deferred until combined hinge/hall logging explains the anomaly (F13). Later evidence also withdrew the keyboard-default part of item 4 (F9) and found that context-free launches, such as LatinIME's permission activity and the permission dialog, open across both panels in the spanned area (F20, M2.5).

1. **Active physical side into WM** (posture service → WM); show that side's tasks, park the other side's (G7b).
2. **Recents:** selection keeps the task's panel (or moves it to the invoking panel per contract); panel-scoped Overview; taskbar off the hinge (G3).
3. **Keyguard occlusion:** `showWhenLocked` / occluding activities in a panel must occlude the keyguard without exposing the other panel (G6).
4. **IME:** ~~soft keyboard on by default (`show_ime_with_hard_keyboard=1`, or fix the input device classified as a hardware keyboard)~~ (withdrawn, see G5 (a)); right→left focus needing a second tap; per-panel `imeVisible`; no panel IME insets to home/spanned windows (G5).
5. **Base image:** WebView provider default + LineageOS WebView + Jelly ([plan §13](dual-screen-plan.md#13-base-image-webview-and-browser-approved)).
6. **Posture service (low priority, defensive):** reject implausible single-update hinge jumps; do not treat low readings as closed unless the hall sensor agrees; log hinge + hall events together.
7. **Hinge overlaps in panel-off mode:** Settings activity-embedding split at x=1392 and the Launcher3 page indicator at 1377–1407 sit inside the 1350–1434 hinge band.
8. **Complete gate coverage** listed above before declaring M2 exit.

## Hinge sensor characteristics (Duo 1, measured)

- Standard `TYPE_HINGE_ANGLE` (Microsoft), no app permission required; on-change, ~10 Hz while moving; reported with two decimals (declared resolution not yet read).
- Range observed: ~2° closed face-to-face, ~180° flat, 360° back-to-back (caps at 360° under pressure).
- Posture service switches to single-screen at ~231–292° folding back and back to dual at ~172–201° unfolding (hysteresis); upright it picks the visible side.
- Microsoft posture sensor requires `surface.permission.USE_OEM_PRIVATE_SENSORS`; the primary hall sensor reports closed only when actually closed face-to-face; a second hall sensor exists with no observed events.
