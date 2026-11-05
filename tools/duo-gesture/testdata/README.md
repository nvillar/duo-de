# Raw touch captures (Surface Duo 1)

Replay fixtures for the three-finger gesture classifier. Each file is the output of
`adb shell getevent -lt /dev/input/event4` (`surface_touchscreen`) on a Duo 1 running the M1 gate image,
captured on 2026-11-04. They contain only touch coordinates, pressure, size and orientation.

Raw range 0–17709 × 0–11411, mapped to the 2784 × 1800 spanned display at rotation 0: panel 0 is
x [0, 1350), the hinge band [1350, 1434), panel 1 [1434, 2784). Positions below are display pixels.

`ReplayFixtureTest` asserts what the classifier reports for every touch (touches are numbered from 0 in
the order they start; the replay prints the same numbers).

## touch3.getevent (12 touches)

| Touch | What was done | Expected report |
|---|---|---|
| 0 | Three-finger pinch-out on panel 0; one finger finally slides off the top edge, after which the controller reports three short contacts at y = 0 in that slot (new tracking ids that resend only X) | SPAN panel 0, commit (`parked`); the parked contacts are ignored |
| 1 | Three-finger pinch-in on panel 0 | CONTRACT panel 0, commit |
| 2 | Three-finger pinch-out on panel 1; the third finger lands 139 ms after the first while the first two already move | SPAN panel 1, commit |
| 3 | Three-finger pinch-in on panel 1 | CONTRACT panel 1, commit |
| 4 | Three-finger swipe from panel 0 across the hinge to the right edge | PUSH 0 → 1, commit (`parked` at the right edge) |
| 5, 10, 11 | One finger dragged from panel 0 across the hinge | nothing |
| 6, 7 | Two-finger touches on panel 0 (meant to be across the hinge), touch 6 with a 20 ms extra contact | nothing (never three fingers) |
| 8 | One finger resting on panel 0 for 290 ms | nothing |
| 9 | Two fingers on panel 0 plus a 110 ms contact at the bottom of panel 1, 11 px from the hinge | CANCEL `edge-start` |

## touch4.getevent (4 touches)

| Touch | What was done | Expected report |
|---|---|---|
| 0 | One finger on panel 0, two on panel 1; the third finger bounces (lands, lifts after 9 ms, lands again at 586 ms); held still for about 6 s, then pinched in toward the hinge | CONTRACT panel 1 (two of the three fingers), commit |
| 1 | Two fingers on panel 0, one on panel 1, held, then spread until the fingers slide off the outer edges | SPAN panel 0, commit (`parked`) |
| 2 | One short tap on panel 0 | nothing |
| 3 | One finger on each panel, held about 3 s | nothing |

The capture's first frame has no `ABS_MT_SLOT` line (the driver was already on slot 0); the replay starts on
slot 0, like the kernel's evdev state.
