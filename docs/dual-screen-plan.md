# First-class dual-screen support for Surface Duo 1

Status: living plan. Sections 1-14 are the original proposal (2026-11-03), revised after a Claude Opus 5.5 / high architecture challenge. Sections 15-17 record the status after M0/M1 and the revised M2 plan (2026-11-04); section 18 records the decision not to distribute images; section 16.5 lists the earlier statements they supersede. Device flashing, wipes and other destructive steps remain explicit operator decisions.
Date: 2026-11-03, updated 2026-11-04.
Target: first-generation Surface Duo, DUO-DE Android 16 r4, vanilla.
Repository: https://github.com/nvillar/duo-de (upstream: Archfx/duo-de).
Installed baseline: v2026.10.28, e68f56779723acb06bfdf11d9bc470d06554c5ba.

## 1. Outcome and scope

Deliver two independently usable app workspaces, not a tablet UI interrupted by a hinge and not two floating windows pretending to be screens. An ordinary unmodified app should work on either panel without awareness of the hinge. Spanning must be an explicit, reversible user action.

The proposed product contract below turns "first-class" into reviewable behavior. These are recommended design decisions for approval, not claims about current stock behavior or instructions to start implementing.

Primary supported configuration: Duo 1 + vanilla + bundled Launcher3/Quickstep. Microsoft Launcher is a supported compatibility target, not a proprietary dependency required for the device to work. Match the useful stock Duo interaction model without promising pixel-perfect reproduction or relying on undocumented Microsoft internals.

In scope: panel task ownership, launches, focus, Home/Back/Recents, move/span/unspan, folding/rotation, input and keyboard, system UI, accessibility, recovery, regression coverage, and build reproducibility.

Out of scope for the first release: a new kernel/vendor stack, restored Microsoft security support, a new launcher from scratch, more than two app workspaces, elaborate desktop/external-display features, app cloning, and bypassing app security restrictions. Preserve normal external-display and non-Duo behavior. Cellular/camera/audio reliability is still a daily-use release gate, but unrelated pre-existing defects are tracked separately.

## 2. Evidence and starting point

The completed source review found four actionable problems:

1. Framework patch 0008 adds panel launch routing after AOSP has already selected the default task area for an explicit launch display ID. Bundled Quickstep supplies display ID 0, bypassing panel routing.
2. Home force-hides tasks. Folding into a single-panel configuration can bypass both restoration paths when relaunch moves the reused task into the shared area.
3. Device patch 0013 classifies a display as spanned using max(width,height) > panelWidth. A single Duo 1 panel at 1350 x 1800 is therefore incorrectly treated as spanning.
4. tools/hinge_lint.py only understands Duo 2 geometry and returns a clean result for unsupported Duo 1 geometry.

The first two require panel mode. The compatibility shim and tooling problems do not. These are source-level findings, not device reproductions; characterize them before changing code.

Known, separate alpha limitations: keyboard remains display-wide; Home uses focused rather than gestured panel; transitions can cross the hinge. Published feature integration evidence is Duo 2/GApps, not Duo 1/vanilla. The reviewed main-16 and a16-dev branches did not contain fixes to this feature.

The architecture challenge identified an additional interaction to address: Launcher3 patch 0001 defaults tablet launches to freeform windowing with explicit bounds, and 0005 corrects those bounds rather than removing the freeform request. Fixing launch-display precedence alone is therefore insufficient. Panel mode must deliberately reconcile freeform requests with its panel-sized app-window contract.

Relevant effective patch sequence:

- frameworks_base/0007: DuoPanelGeometry.
- frameworks_base/0008: DuoDisplayAreaPolicyProvider, PanelGroup, LaunchParamsUtil, TaskDisplayArea, ActivityTaskManagerService and RootWindowContainer hooks.
- device_phh_treble/0007 through 0013: Microsoft compatibility library; later patches supersede earlier DisplayMask and PaneManager behavior.
- packages_apps_Launcher3/0005 and 0006: panel geometry and drawer layout. Do not fix the already-superseded drawer-background bug again.

## 3. Proposed behavior contract

| Area | Expected behavior |
|---|---|
| Launch from home | A tap on a panel opens/reuses the app on that panel. Explicit task-area requests take priority. Explicit external-display requests stay external. |
| Launch from an app | Ordinary child activities stay with the initiating task/panel. Respect Android launch modes, task affinity, multi-instance support and explicit placement. |
| Notifications and context-free launches | Choose the current foreground interaction panel using a documented fallback when no initiating panel exists; preserve Android task identity and permissions. |
| Home | Home on a panel reveals home on that panel only. The other panel's task remains visible and usable. |
| Back | Back targets the task receiving the gesture; preserve predictive Back and app-defined back behavior. |
| Recents | The invoking panel supplies the destination for a selected task. For first-class v1, keep the other panel usable. A temporary whole-display overview is acceptable only in the engineering prototype, not evidence that this v1 contract is satisfied. |
| Move | An explicit move action transfers the same task to the other panel, preserving back stack and app state. Proposed default: replace and park the displaced task, never finish it; swap is a separate later action. Never silently duplicate a single-instance app. |
| Span/unspan | Explicit action moves the task between a panel and the shared area. Restore the original panel on unspan when available. Non-resizable/incompatible apps get Android-compatible fallback or an explicit unsupported action, not forced distortion. |
| App pairs | Once individual placement is correct, a saved pair opens the two tasks on assigned panels without depending on Microsoft Launcher. |
| Fold to phone mode | Proposed default: the enabled physical panel keeps its own task; the other panel's tasks are parked, not deleted or silently migrated. Unfold restores each panel. A spanned task adapts to the enabled panel and re-spans only if the user has not moved/replaced it in the meantime. Confirm this default against user expectations before production implementation. |
| Rotate | Follow physical panel identity, not "left means x=0" assumptions. Apply one coherent topology transition; no transient touch on a hidden panel. |
| Keyboard | One active input connection. Keyboard follows the focused editing panel; the other panel stays usable. Explicit tabletop keyboard-on-other-panel is a later interaction mode, not the default. |
| System UI | Notification shade, permission dialogs, toasts, PiP and critical controls remain visible and reachable; never position required actions under the hinge. |
| Lock/unlock | Both lit panels are covered by keyguard. No app content or actionable surface leaks on the other panel during authentication. |
| Accessibility | TalkBack focus, magnification, large text, pointer/keyboard traversal and switch access remain coherent across panels and posture changes. |

Additional proposed defaults, to validate during the prototype:

- Interaction panel is established by purposeful pointer/keyboard interaction; system gestures establish it from their origin. Do not equate any incidental focus change with a user panel choice.
- A notification launch uses the panel that opened the shade, otherwise the last valid interaction panel.
- Bundled Launcher3 ignores its legacy freeform preference in panel mode. Audit and deliberately reject or translate unsupported split/freeform/desktop requests; never silently leave overlapping competing workspace modes. External displays keep normal policy.
- In dual posture, physical orientation controls the shared display. A single app's orientation request should use compatible panel-level letterboxing rather than rotate both workspaces; preserve normal supported phone behavior in single-panel posture. Verify Android 16 size/orientation rules at the effective density.
- Start with panel navigation controls rather than a full per-panel taskbar. Keyboard/mouse and accessibility need equivalent actions.
- "Open adjacent" should use the other panel where the app's task/launch semantics permit; unsupported cases must be intentional, not accidental fallbacks.

Remaining UX choices: visual form of panel-scoped Recents and All Apps, precise move/span affordances, and user confirmation of fold/restore behavior. Record the decisions before their production milestone. These choices do not block source/build preparation or characterization.

## 4. Architecture recommendation and decision gate

### Preserve one logical display with panel task areas, provisionally

Retain the current per-panel DisplayAreaGroup/TaskDisplayArea approach as the starting architecture, with a shared spanning area and one spanning home. Avoid introducing two virtual displays: that adds display identity, IME, mirroring, input, compatibility and security work without evidence it is necessary.

However, do not declare the current topology production-ready. An early, bounded prototype must establish that AOSP WM Shell/Quickstep, transitions, Back and IME can cooperate with sibling panel areas. If they cannot, compare a Shell-organized root-task approach on the same display before expanding the patch set. M1 contains explicit pass/fail and pivot criteria.

Use AOSP StageCoordinator, SplitLayout, DisplayImeController, ShellTaskOrganizer and transition handling as references, not a drop-in stock split-screen replacement. Stock split-screen's paired lifecycle is not our independent-workspace contract. Pin all adaptation to android-16.0.0_r4; examples from newer branches are design references only.

### Critical integration questions to answer first

These are architectural hypotheses from Opus, not additional confirmed device defects:

- Does r4 Shell's root-task-area organizer observe the vendor-ID panel areas? If not, add a narrow panel-aware Shell integration instead of expecting default-display code to discover them automatically.
- Do transition leashes escape the PanelGroup surface crop? Inspect both animation and input bounds.
- Quickstep Overview is a state of the HOME activity, while an alternate launcher's fallback Recents may be a non-home activity. Test both against the patch's home/non-home z-order rule. A single spanning launcher also has a single All Apps/Overview state machine; scoping rendering and interaction is real work.
- Can a platform IME-placeholder/group mechanism relocate the IME container to the editing panel while publishing correct insets? Verify the relevant r4 APIs and feature hierarchy before relying on this candidate mechanism.
- Back targets focused windows, while gesture detection starts at display edges. Resolve hinge-side edges, gesture-origin focus and predictive-animation ownership coherently.
- Does the home/wallpaper continue drawing needlessly under two occupied panels? Measure before accepting a battery cost.
- Test keyguard with a showWhenLocked activity on one panel and ordinary content on the other. Display-wide occlusion is a safety gate.

A Shell-side panel adapter is part of the primary candidate, not merely a late fallback. It must not become a collection of unrelated special cases in every handler.

### One topology contract, clear ownership

- Geometry: physical panel rectangles, hinge region, logical-display bounds, rotation, scaling and coordinate transforms. Extend DuoPanelGeometry rather than add another width heuristic.
- Posture: authoritative hardware state from the existing posture processor, including active physical side. A resized rectangle alone cannot distinguish which screen is enabled.
- Task policy: task-to-panel ownership, span state, restore destination and explicit transition reasons. Prefer existing AOSP task state and transactions; add only policy-specific state with a clear lifecycle.
- Focus/gestures: resolve the physical gesture origin and task target before acting. Avoid faking a focus tap to route navigation.
- Compatibility adapters: DisplayMask and PaneManager derive from the same semantic contract. Honor app-window coordinates and rotation. Do not turn on Microsoft's currently disconnected PaneManager path until it can report real ownership/focus.
- System UI/IME: window-manager/input owners control surface placement and insets; the launcher must not be the authority for arbitrary app windows.

The framework and SDK compatibility library have different build/classloader boundaries. A shared contract does not automatically mean both can link the same Java class. Prefer a pure geometry module where feasible, otherwise generated common specifications plus cross-implementation conformance tests.

### Topology transitions

Proposed transaction sequence: capture physical state and task ownership; resolve target topology and focused task; calculate destination bounds and input regions; apply task/surface changes through normal WM/Shell transitions; update focus and insets; notify compatibility consumers; verify visibility and clear temporary state.

Handle repeated/interrupted folds, task removal/process death, screen-off, service restart, and stale callbacks. No permanent force-hidden state or global "currently changing" flag that can strand tasks. Explicitly decide what state survives launcher/system-server restart versus a full reboot.

## 5. Characterization and prerequisites (M0a / M0b)

### M0a - Learn from the already installed alpha before paying the build cost

The installed release already contains the opt-in policy. First record a panel-off baseline and plan an explicitly user-approved toggle/reboot experiment. No custom build is required to observe current Recents, navigation, IME and folding limitations.

Capture system build identity (not only the stock-looking vendor fingerprint), relevant mode properties, display/input/window/task state, default HOME/IME, navigation mode, density, posture and launcher version. Exercise the same small two-app smoke set with panel mode off and, if safely authorized, on.

Before any toggle, verify the flag-reset route and a fallback if Android/ADB does not become usable. Both persist.sys.phh.duo.panels and persist.debug.phh.duo.panels can enable the policy; record both and reset the actual enabled source using authorized capabilities. A reboot or system-image reflash alone is not guaranteed to clear properties persisted in userdata. A factory reset may be a last-resort recovery and requires explicit data-loss approval. Do not change USB authorization/security policy to manufacture an escape route.

Stop the experiment if no acceptable recovery route exists. Characterization may demonstrate the documented alpha failures; passing is not expected and it is not permission to daily-drive the alpha.

### M0b - Reproducible custom-build and recovery foundation

This repository is a patch/build harness, not a complete Android checkout. The current Windows clone is suitable for review and patch authoring; a dedicated Linux Android build environment is required for the system image.

Observed build details:

- build.sh and clean-and-clone.sh target android-16.0.0_r4, with patch application order trebledroid -> personal -> duo.
- patch.sh applies each project directory's patches in lexical order with git am -3.
- build/default.xml points many dependencies at moving branches, including Archfx/duoPosture, duoOverlays, duoTreble and duoVendor. The release tag alone does not freeze these sources.
- build.sh currently builds both GApps and vanilla, assumes setup/patching has already happened, and uses the treble_arm64_bvN-bp2a-userdebug lunch target for vanilla.
- sync.sh updates TrebleDroid patch inputs; it is not the reproducible developer build entry point.
- clean-and-clone.sh contains reset/clean operations. Never run it against the primary checkout or a shared Android tree.
- build/aosp.mk points the updater at Archfx's feed; generateOta in build.sh also constructs upstream release URLs.
- Dockerfile uses Ubuntu 22.04 and Java 17, but is a reference to audit, not a verified ready-to-use environment. It sets an upstream author's Git identity and uses network installation scripts.
- Release signing expects an external Archfx private-key directory. Those keys are not ours and must not be assumed available.

M0 deliverables:

1. A resource and access check for a dedicated Linux host: storage, memory, CPU, artifact transfer, approved dependency feeds and required Android repositories. Do not provision cloud spend or bypass managed-device feed policies. Use user-scoped uv for any new host Python utilities.
2. A frozen source manifest, toolchain/container identifier, patch-series commit and artifact checksums. Seek the original release dependency revisions; if unavailable, label the build a new baseline, not a byte-identical reproduction.
3. A safe, explicit developer recipe that builds vanilla only, leaves primary checkouts alone, produces unique build identifiers, and does not upload/release by default. Stabilize the baseline before changing Android versions.
4. A signing/data compatibility decision: inspect installed package certificates and build tags read-only where available, then document development keys, privileged/shared-UID signatures and future update strategy. Expect the first custom image to require a clean wipe unless compatibility is demonstrated; build tags alone are not proof. Do not promise in-place upgrades between differently signed builds.
5. Disable or isolate experimental builds from the upstream OTA feed through a visible, tested updater configuration. No unattended cross-channel updates.
6. Known-good released vanilla image retained locally with checksum, exact current partition/slot record, a tested recovery/fastboot path, and an operator rollback runbook. Do not assume an untouched inactive stock system: the installation removed system_b.
7. Acquire the matching Microsoft recovery ZIP if available before claiming return-to-stock is possible. Its availability for this device remains unconfirmed. The minimum experimental fallback is the retained working DUO-DE image, not a promise of Microsoft restoration.
8. Preserve LF line endings and review the exact exported patch series. Pinning external dependency SHAs does not require making companion forks. Do not assume a source commit alone proves which framework binary is on the device.

Device flashing, factory resets, driver/host changes and any destructive rollback remain separate user-authorized actions. Keep the bootloader unlocked with custom images; verify actual bootloader state in fastboot rather than relying solely on ROM properties. Do not use the sole working device for experiments without a viable recovery path.

M0a exit: characterize the released image and document baseline/alpha observations, or explicitly record that toggling is blocked by safety. M0b exit: an unmodified-behavior self-built baseline boots on Duo 1/vanilla; provenance and signing limitations are recorded; operator can reach recovery and has the validated known-good image and instructions. Do not claim a rollback was exercised unless it was actually performed with authorization.

Do not routinely push replacement framework jars onto the working device: it complicates signatures, optimization, rollback and provenance. Prefer coherent development images in this first plan. Do not collect modem/persist/identity partitions merely as a precaution; that is unnecessary scope with device-specific and privacy risks.

## 6. Milestones, dependencies and exit gates

### M1 - Earliest architectural proof, before production foundations

Use M0a to learn cheaply; M0b is needed for modified-framework experiments. Keep exploratory changes in a disposable branch, not in the production patch sequence. Source-only work cannot satisfy this gate.

Create the smallest vertical slice: two panel-sized apps, fullscreen launcher requests, valid panel targeting, an explicit origin-aware navigation path, and a candidate platform IME-container placement. Temporary launcher bypasses can isolate the experiment but must not be mistaken for the final framework routing fix.

| Gate | Required evidence on Duo 1 / vanilla |
|---|---|
| G1 Launch/reuse | Ten launches/reuses per side reach the intended panel task area. Include explicit display ID 0 and a panel-off control. Record requested windowing mode and final bounds. |
| G2 Independent Home | Home on either panel preserves the other panel's task, visibility and input. For simple multi-resume-capable probes, confirm lifecycle as well; do not assume every app stays RESUMED by contract. |
| G3 Recents/All Apps | Quickstep home and alternate-HOME fallback are visible and interactive. Select/cancel returns to the invoking panel without losing the other task. Demonstrate panel-scoped presentation before claiming the v1 contract; global Overview is only a provisional prototype result. |
| G4 Back/focus | A gesture from A targets A even when B was previously focused. Confirm predictive Back target, animation bounds, cancel path and hinge-side gesture behavior. |
| G5 IME | Keyboard frame and input agree; only the intended task gets relevant IME insets; the other panel remains touchable. Switch editor focus and rotate with keyboard open. |
| G6 Authentication safety | Keyguard blocks both panels over repeated lock/unlock cycles, including showWhenLocked on one panel. No ordinary content/input leaks on the other. |
| G7 Physical posture | Ten physical folds toward each side, plus rotation: no lost task, stranded hidden flag or input on the extinguished panel. Results follow the proposed fold policy. |
| G8 Transitions | Relevant surfaces and input regions stay within their intended panel except an intentional span operation. Investigate home/wallpaper redraw cost. |
| G9 Stability/escape | No attributable system_server/SystemUI crash during the bounded smoke set; mode can be disabled and normal panel-off operation restored. |

Use window/task/input dumps, lifecycle-aware probe apps and WM/Shell traces alongside visual observations. Touch and physical fold outcomes cannot be inferred from screenshots alone.

Pivot/stop criteria:

- G6 failure is a safety stop, not an accepted alpha limitation.
- A failure confined to routing, geometry or temporary hidden state calls for a local fix, not an architecture rewrite.
- If Recents/Back/IME cannot satisfy the contract without scattered invasive changes across unrelated per-display handlers, compare candidate B: Shell-organized independent panel root tasks within the default task area, with the same gate and independent-lifecycle requirement.
- If a contained Shell adapter can satisfy those invariants, keep the DisplayAreaGroup design. Document the r4 APIs and traces supporting that decision.
- If neither candidate supports panel-local input/IME/keyguard safely, stop broad feature work and write a narrower architectural decision before considering two logical displays.
- Do not equate a global-Overview prototype or a keyboard crop with architectural success for first-class v1.

Topology-independent work can proceed alongside this gate: Duo 1 hinge_lint support and the PaneManager classification regression. Those fixes improve current default mode/diagnostics regardless of the eventual task topology.

### M2 - Production foundations and topology lifecycle

Depends on M1's recorded architecture decision. Replace experimental bypasses with maintainable framework changes and tests.

- Fix routing precedence inside the selected display while preserving explicit task-area and external-display requests. Do not merely strip all launcher options.
- Deliberately reconcile Launcher3's tablet freeform setting and Android's split/freeform/desktop requests with panel mode.
- Fix restoration of tasks hidden by this policy independently of route/destination. Do not clear another organizer's hidden reason; prefer normal AOSP visibility operations or explicitly owned state with cleanup.
- Land reviewed regressions for PaneManager geometry and unsupported-device diagnostics if not already done.

- Define left-only/right-only/dual/spanned states, rotations, hinge-gap settings and coordinate transforms.
- Obtain active side from posture state instead of guessing from width.
- Replace "both task groups fill the same single screen" behavior with an explicit active/parked task policy.
- Implement bounded move/span/unspan operations as task transactions; preserve identity/back stack and app compatibility.
- Demonstrate two independent tasks, per-panel Home, input focus, proper resume/stop behavior, process death restoration and fold/unfold recovery.
- Expand the successful M1 Quickstep/Back/IME slice without weakening its gate.
- Check display-wide SystemUI/root area layering so launching Recents or a system surface cannot leave the panels unreachable.

Code locations: frameworks_base/0007 and 0008 and relevant Shell hooks; device_phh_treble/0013 and effective SDK implementation; Launcher3 launch options; tools/hinge_lint.py; Android wmtests and host geometry tests.

Exit: all four reviewed findings have regression coverage/fixes; a two-app scenario survives fold, rotate, Home and relaunch with no lost/invisible task, wrong input destination or overlapping active stacks; panel-off behavior remains unchanged.

### M3 - Independent navigation and launcher integration

Depends on M2.

- Introduce panel-aware gesture hit regions and explicit routing to the intended task.
- Integrate Home, predictive Back, app switching and Recents with Quickstep/Shell rather than suppressing task visibility with ad hoc callbacks.
- Supply visible move and span controls first; add polished drag gestures only after command semantics are reliable.
- Implement app-pair launch after individual placement and reuse are correct.
- Respect one-spanning-home behavior; home must remain touchable on an empty panel while another task occupies the other panel.
- Verify Microsoft Launcher as an alternate HOME with a recorded version, without making it the navigation authority.
- Specify keyboard/mouse actions, large touch targets, accessibility labels and traversable focus order.

Exit: from either panel the user can launch, go Home, return from Recents, go Back, move and span/unspan without disturbing the other task except where explicitly documented.

### M4 - Keyboard, insets and system surfaces

Depends on M2; integrate with M3 before acceptance.

- Trace IME ownership, focus, target window, insets and animation surfaces before moving/cropping anything.
- Implement active-panel keyboard bounds and app-visible insets through platform mechanisms. Avoid shrinking the rendering surface while leaving stale full-display touch regions or full-display app resize insets.
- Exercise bundled keyboard first, then a second IME where available; include multiline text, password fields, suggestions, handwriting, physical keyboard and switching edit focus across panels.
- Make permission dialogs and other critical actions reachable off the hinge; handle toast, PiP, notification shade, status/task bars and lockscreen layouts.
- Distinguish activity-hosted dialogs from display/window-hosted system surfaces. Include ANR/app-error UI, biometric prompts, power/volume controls and media-projection consent, not just ordinary permission activities.
- Keep authentication and secure-window behavior correct across both surfaces; preserve screenshot/recording restrictions and touch isolation.
- Add tabletop keyboard-on-other-panel only after the default active-panel experience is reliable.

Exit: typing and modal interactions work in all supported postures without hiding content or sending input to the wrong task. Authentication blocks both panels correctly.

### M5 - App compatibility, accessibility and behavior polish

Depends on M2-M4.

- Reconcile app-facing Jetpack FoldingFeature/DisplayMask semantics: hinge occlusion, separation, rotation, window-local coordinates and single-panel windows that should not see a hinge.
- Validate compatibility-mode/non-resizable apps, games/fullscreen media, camera, document pickers, permission prompts, PiP and multi-instance apps.
- Remove remaining animation bleed across the physical hinge while preserving valid spanning transitions.
- Validate TalkBack, magnification, large fonts/display scaling and physical keyboard traversal.
- Verify panel-off mode and non-Duo gating; do not extend Duo 1 claims to Duo 2 without hardware evidence.
- Improve logging/error messages and expose mode switching only with clearly stated restart/recovery behavior.
- Validate the failure escape: an operator-accessible kill switch and, if safely implementable, a bounded boot/crash guard that disables panel policy before repeated framework restart. Specify which process owns it, how persisted properties are cleared and how it fails closed. Never recover by bypassing keyguard or weakening app isolation.

Exit: the acceptance matrix below passes for the supported device/variant/launcher set; any exceptions are visible, justified and not on core dual-screen flows.

### M6 - Soak, update/recovery and daily-use qualification

Depends on M0-M5.

- Record boot/sleep/wake, sustained typing, screen flipping, repeated notification launches, thermal behavior, standby drain and app switching against the same-build panel-off control.
- Verify upgrade and fallback for the actual signing/channel configuration. Preserve provenance and remove unnecessary debug behavior; developer options are not a production safety mechanism.
- Run relevant framework/Shell/launcher suites, accessibility checks and available compatibility tests. No claim of CTS certification without a full applicable run.
- ~~Publish limitations, known-good artifact hashes and recovery instructions alongside any candidate.~~ Superseded by §18 (no image distribution): the operator keeps provenance and checksums of the images they build; the repository publishes limitations and recovery instructions in its docs. Push only with the fork owner's personal GitHub account.

Suggested release gates (proposals, not completed measurements):

- 100 automated repetitions of critical synthetic launch/Home/relaunch/rotation/topology scenarios, with zero stranded tasks, wrong-panel input, critical hinge overlaps, crashes or ANRs attributable to panel mode. These do not replace physical sensor tests.
- Separately, logged manual physical fold/flip sessions toward both sides, including keyboard-open and screen-off cases; at least the M1 ten-per-side physical gate for every candidate, plus real-use soak. Do not claim physical folds were scripted without a test fixture.
- At least seven consecutive days of ordinary Duo 1/vanilla use with the default launcher, covering daily sleep/wake, typing, camera/media and notifications; repeat enough with Microsoft Launcher to qualify its separate compatibility claim.
- No repeatable performance or battery regression beyond the predeclared baseline noise envelope; set numerical budgets from M0 measurements, not invented absolute targets.
- Every high-severity issue closed, reproducibly waived as an unrelated baseline defect, or a release blocker. No waiver for lost input, invisible tasks, lockscreen leaks or unrecoverable device state.

Qualify a declared app/probe set plus the user's required applications, not "all Android apps." Validate the Duo 1 vanilla base ROM with panel mode off separately. Record camera/radio/keyboard baseline failures even if not caused by the new feature.

## 7. Test design

Use targeted test layers instead of assuming arithmetic tests prove window-manager correctness.

| Layer | Cases |
|---|---|
| Pure host geometry | Duo 1 and Duo 2 specs; four rotations; left-only/right-only/dual; real hinge/gapless; density/scaling transforms; invalid/unknown sizes. |
| Framework routing/lifecycle | Display ID precedence; explicit TDA token/feature; external display; new/reused task; no source bounds; child/deep-link/notification starts; hidden-reason ownership; Home/fold/relaunch; process death. |
| Shell/Quickstep | Per-panel gestures, root-task ordering, transition cancellation, Recents selection, Back dispatch, move/span/unspan and app-pair launches. |
| IME/SystemUI | Focus changes with keyboard visible; app insets; keyboard rotation; secure windows; both-panel keyguard; permission-dialog reachability; PiP and notification interaction. |
| Device scenarios | Panel A app + panel B app; fold toward either panel; rotate during transition; screen-off during transition; kill/restart a background app; swap launchers; reconnect input devices. |
| App probes | Unmodified single-window app, fold-aware sample, non-resizable app, camera, browser, document picker, media/PiP, Microsoft compatibility probe and accessibility service. |
| Negative controls | Panel mode disabled; unsupported device/size returns explicit unsupported; explicit external-display launch never diverted into a Duo panel. |

Record initial state, actions, expected visible tasks, input target, geometry and final state. Prefer WindowManager/Shell traces and bounded structured logs, plus screenshots with physical hinge bands; screenshots alone cannot detect input/focus correctness. Minimize captured personal content.

Mandatory end-to-end invariants: each visible panel has one intended foreground task (unless explicitly shared); input follows visible placement; hidden/parked tasks are restorable; no phantom second pane in phone mode; no critical controls overlap the physical hinge; topology change does not lose task identity.

## 8. Implementation slices and ownership

Implement only after plan approval, in isolated worktrees. Suggested review-sized commits/branches, not an instruction to create PRs:

1. Released-image characterization and reproducible build/channel safeguards.
2. Duo 1 diagnostics, PaneManager regression and geometry correction.
3. Disposable architecture proof and an explicit keep/pivot decision.
4. Production launch-routing and freeform-mode reconciliation.
5. Task restoration/visibility ownership fix.
6. Posture/topology and parking policy.
7. Navigation/Quickstep and explicit move/span controls.
8. IME/insets integration.
9. SystemUI/lockscreen/accessibility.
10. App compatibility, transition polish, release evidence.

The topology-independent parts of slice 2 can proceed during build preparation/proof. Do not polish production routing and lifecycle patches until slice 3 makes the topology decision. Serialize integration against the same framework patch series; many milestones touch the same WM classes. M3 and M4 can be researched in parallel after M2, but are not independent acceptance units.

Default to carrying focused, reviewable patches in this fork. If sustained edits to duoPosture or duoOverlays are needed, propose explicit companion forks and pin them in the manifest; do not silently modify or depend on upstream moving branches. Patch authoring occurs in disposable Android source checkouts and is exported back to this harness; validate clean application from the frozen baseline.

## 9. Risks and stop conditions

- No usable build host or dependency access: stop at source work; do not present an unbuilt patch as ready for flashing.
- Release dependencies cannot be reconstructed: establish and label a new reproducible baseline, with independent baseline regression testing.
- Signing mismatch: plan a clean install with explicit data-loss approval; never assume data-compatible downgrade.
- Missing reliable rollback: no experimental flash.
- WM/Shell incompatibility with sibling task areas: pause M3-M5 and make the architectural decision at M2.
- Unknown first-gen hardware behavior: keep claims restricted to source analysis until device evidence exists.
- Stock-like look with wrong focus/lifecycle: not an acceptable shortcut.
- Daily use includes unrelated camera/radio regressions: track separately, but do not label the complete ROM daily-ready while core required functions remain broken.
- Old kernel/vendor security remains: describe this as dual-screen UX improvement, not restored security maintenance or suitability for company-managed sensitive workloads.

## 10. References

- [Dual-panel implementation and declared gaps, upstream PR 294](https://github.com/Archfx/duo-de/pull/294)
- [Microsoft Launcher compatibility, PR 279](https://github.com/Archfx/duo-de/pull/279)
- [Effective DisplayMask integration, PR 283](https://github.com/Archfx/duo-de/pull/283)
- [Launcher geometry fixes, PR 295](https://github.com/Archfx/duo-de/pull/295)
- [Installed release](https://github.com/Archfx/duo-de/releases/tag/v2026.10.28)
- [AOSP WM Shell](https://github.com/aosp-mirror/platform_frameworks_base/tree/main/libs/WindowManager/Shell)
- [AOSP multi-display launch policy](https://source.android.com/docs/core/display/multi_display/activity-launch)
- [AOSP IME behavior](https://source.android.com/docs/core/display/multi_display/ime-support)
- [Jetpack fold/hinge semantics](https://developer.android.com/develop/ui/compose/layouts/adaptive/foldables/make-your-app-fold-aware)
- [Android WindowManager samples](https://github.com/android/platform-samples/tree/main/samples/user-interface/windowmanager)
- [Microsoft Kotlin samples, archived](https://github.com/microsoft/surface-duo-sdk-samples-kotlin)
- [Duo posture reference](https://github.com/thai-ng/PostureProcessor)
- [Thor display/session architecture reference, archived prototype](https://github.com/dakotadawolfe/thor-dual-screen-launcher)

## 11. Prototype versus first-class v1

The engineering prototype demonstrates feasibility. It may use explicit move/span buttons, a basic navigation surface, static wallpaper and provisional whole-display Overview. It is not labelled daily-ready.

First-class v1 requires the full core contract: independent workspaces, dependable panel-local navigation/input, reachable critical system surfaces, fold/restore correctness, keyguard safety, accessibility and baseline hardware reliability. A polished drag gesture, saved app pairs, tabletop keyboard on the opposite panel, richer taskbar and per-panel screenshots may follow after the core experience; none may conceal broken task lifecycle.

Microsoft Launcher gets a separate compatibility qualification. Support it if feasible without changing the core architecture, but do not block a complete bundled-launcher experience on reproducing proprietary Microsoft services. For v1, explicitly state which alternate launcher/version combinations passed.

## 12. Rubber-duck review and disposition

Reviewer: Claude Opus 5.5, reasoning effort high, requested by the project owner. Review was read-only and did not interact with the device.

| Challenge | Decision incorporated |
|---|---|
| Build-first sequencing wastes a cheap existing-image experiment. | Added M0a characterization of the shipped alpha, with explicit consent and flag-reset/data-loss safeguards. |
| Launcher3 still requests freeform windows by default. | Added a panel-mode/freeform contract, launch-option observations and production reconciliation. |
| Shell may not know vendor panel task areas; transition crops may be escaped. | Added explicit Shell integration to the primary candidate and r4 source/trace verification in M1. These remain hypotheses until checked. |
| Quickstep and fallback Recents have different HOME/non-HOME layering; one launcher has one state machine. | Elevated Recents and All Apps to an early architecture gate; whole-display Overview cannot count as first-class success. |
| Back origin, focus and edge regions are coupled. | One interaction-panel policy and a predictive-Back/hinge-edge gate, rather than unrelated Home and Back hacks. |
| IME may have a native group-placement mechanism. | Investigate r4 IME placeholders/container relocation before inventing surface-cropping workarounds. |
| Keyguard and display-centred critical dialogs were too late. | M1 authentication safety gate; expanded system-surface inventory. |
| First custom image probably cannot preserve data with different keys. | Expect a clean wipe unless certificate compatibility is demonstrated; no promise of stock keys or in-place upgrades. |
| Physical folds cannot simply be "100 scripted repetitions." | Split synthetic automation, manual physical posture gates and sustained everyday soak. |
| Readiness lacked a defined escape mechanism and app scope. | Kill-switch/crash-guard design and declared app/launcher/device qualification, not universal compatibility. |

Not adopted without further evidence: routine framework-jar remount/push on the working device; raw modem/persist backups; treating inferred r4 mechanisms as verified APIs; claiming that copying or inspecting a single binary proves a complete reproducible source baseline. These either expand risk or overstate the evidence.

Final architectural position: retain one logical display provisionally, but earn that choice with a small, falsifiable Duo 1 experiment before investing in broad UI implementation. No engineering-time estimate is credible until M0b build viability and M1 architecture feasibility are measured.

## 13. Base image: WebView and browser (approved)

Separate from the dual-screen milestones; a daily-use prerequisite for the vanilla image.

Problem found on the installed release: Android had no active WebView provider. AOSP WebView (`com.android.webview`, Chromium **133**) is installed and valid, but the framework default points to the absent `com.google.android.webview`, so every WebView-using app crashed ("No WebView installed"). Selecting it manually with `cmd webviewupdate set-webview-implementation com.android.webview` fixed it on the device. The bundled engine is also far behind Chrome Stable (158 at the time of writing).

Decision (owner-approved): no app store is planned, so updates arrive only with image builds.

1. Replace the stale WebView prebuilt with **LineageOS's prebuilt Chromium WebView** (`LineageOS/android_external_chromium-webview_prebuilt_arm64`, Git LFS; same `com.android.webview` package; unmodified Chromium build settings). Pin the commit and APK SHA-256 in the dev manifest; record the Chromium version in provenance.
2. Make `com.android.webview` the default/fallback provider via a framework overlay, so fresh installs never end up with no provider. Verify with `dumpsys webviewupdate` and a WebView-using app.
3. Add **LineageOS Jelly** (Android 16 branch `lineage-23.2`) as the basic browser; it uses the system WebView, so its engine moves with item 1. Confirm its build integration (Soong vs Gradle) on r4 and its license files before shipping.
4. Before each image build, check for a newer LineageOS WebView; credit LineageOS for the WebView and Jelly and keep their license notices. Images are built by the operator, not distributed (§18).

Not chosen: Cromite (privacy/ad-block focus not needed; lags Chrome Stable; JIT off by default), Vanadium (GrapheneOS-specific), Chrome/Brave/Edge (not redistributable).

## 14. Initial read-only device baseline

Once the USB-connected device was available for testing, the following was inspected without changing settings, rebooting or launching apps:

- USB debugging is authorized and Android reports boot complete.
- Surface Duo, Android 16, display build `treble_arm64_bvN-userdebug 16 BP4A.251205.006 261028`.
- Both panel-mode properties are unset: experimental panel mode is still off. Hinge-disable property is `0`.
- Current display size is 2784 x 1800 at density 400.
- Default HOME is `com.android.launcher3/.uioverrides.QuickstepLauncher`.
- Default IME is `com.android.inputmethod.latin/.LatinIME`; navigation setting is `2`.
- Battery reports 93% and charging.
- Even `ro.system.build.fingerprint` reports the old stock Microsoft fingerprint, while the display build identifies Android 16. Fingerprints/build tags are therefore insufficient evidence of signing or source identity on this ROM.

This confirms device availability, not feature correctness. No panel-mode toggle, reboot, flash, wipe, or implementation has been performed as part of this planning task.

## 15. Status after M0/M1 (2026-11-04)

- **M0 done.** Reproducible vanilla build harness (Podman on WSL2), pinned manifest, test-key signing, OTA disabled, provenance. Unchanged baseline built, flashed and smoke-tested on Duo 1. Setup guide: [development-setup.md](development-setup.md).
- **M1 exploratory slice completed; architecture retained provisionally.** Device evidence establishes basic panel launch/reuse, focused-panel Home, outer-edge Back dispatch and panel-sized IME placement ([m1-gate-report.md](m1-gate-report.md)). G3 (Recents) and G7 (folding) remain contract failures; secure-PIN authentication, transition containment (G8), gesture-origin Home with the other panel focused, predictive-Back cancellation and hinge-side Back remain unverified. No demonstrated result requires a pivot, but **architecture closure depends on the bounded Recents/transition proof in M2.0–M2.2.** An actual authentication leak remains an immediate stop.
- **M2.0 consolidation and warm-build measurement done** (gate collector and risk re-checks open): baseline and gate build commits tagged `m0-baseline-image` (`0b04938`) and `m1-gate-image` (`bfb0d8b`); everything consolidated on branch `duo-integration`.
- Reproducibility findings: patched AOSP projects need full history plus `android-16.0.0_r1..r3` tags (patch bases); two upstream patches were regenerated for a clean r4 tree; interrupted WSL builds can corrupt `out/` (clean rebuild); keep the host awake.
- Upstream overlap: mkostersitz's unmerged Launcher3 routing, setup-on-first-panel, keyboard-per-panel and launcher display-size patches are integrated (authorship kept) in the gate branch.
- Build timings on the reference host (Windows 11 + WSL2, 20 threads, 64 GB assigned to WSL), full system image each: clean vanilla build 5 h 49 min (188k steps); the gate rebuild took 1 h 07 min (22k steps, ~25 min Soong analysis) with new patches, the wrapper's default `installclean` and a new build number. Measured warm builds (same Podman setup, `--with-probe --no-installclean`): no-op 2 min 24 s (112 steps); touch-only `services` / Launcher3 sources 2 min 08 s / 3 min 45 s; a real `services` bytecode change (one added constant in a WM class) 3 min 17 s (227 steps, Ninja 2 min 42 s). The M2.0 warm-build measurement item is therefore done; `--no-installclean` images can keep stale files, so milestone evidence still uses full `installclean` images.

## 16. Findings ledger and M2 (revised after Astra review)

### 16.1 Ledger

Status vocabulary: *implemented* (patch exists) → *built* → *device-verified* (seen on Duo 1) → *regression-complete* (repeatable test + full coverage).

| # | Finding | Status / disposition |
|---|---|---|
| F1 | Quickstep `launchDisplayId=0` bypassed panel routing (P1a) | Device-verified (fw 0011, mkostersitz), Launcher3 path only. Routing still depends on HOME source + LAUNCHER + source bounds → policy closure in M2.5. |
| F2 | Home → fold → relaunch left a task force-hidden (P1b) | Implemented/built (fw 0014); `Restored minimized` seen on non-fold relaunch; **fold path unverified** → M2.1/M2.5. |
| F3 | PaneManager misclassified Duo 1 single panel (P2) | Built (device_phh_treble 0015); needs direct functional coverage (getPaneStates in each posture) plus Surface Camera check → M2.5. |
| F4 | hinge_lint false-clean on Duo 1 (P3) | Regression-complete (offline tests). |
| F5 | Launcher3 requested freeform in tablet mode | Device-verified for Launcher3 only (L3 0008 + fw 0016). Framework policy for arbitrary split/freeform/desktop requests still undefined → M2.5. |
| F6 | Single-screen: WM has no active-side input; right-only shows left panel's app; other side not parked | Device-verified failure → M2.1. Verified in posture service source: both single-screen sides map to the same DeviceState ID (FOLDED); DeviceState is requested before size/offset/composition, not as one transaction; manual posture selection bypasses that path. |
| F7 | Recents: whole-display Overview; selection reparents task into the spanned area; taskbar centred on hinge | Device-verified failure → **M2.2 first**. Contract (§3): selection goes to the **invoking** panel; document any exception. |
| F8 | `showWhenLocked` in a panel does not occlude keyguard | Device-verified functional failure → M2.3 (keyguard occlusion is per display in r4). |
| F9 | ~~Soft keyboard suppressed by default (an input device is treated as a hardware keyboard)~~ | **Withdrawn.** No physical keyboard is present: `dumpsys input` lists only the Virtual input device and the configuration reports `nokeys`. With the default `show_ime_with_hard_keyboard=0` the soft keyboard appears on the first tap. The gate observation was F10. No base-image keyboard-default change. |
| F10 | IME: right→left editor focus needs a second tap | Device-verified → M2.4. Left→right and same-panel focus changes show the keyboard on the first tap; this asymmetry is the real keyboard defect. **Corrected:** non-target apps reporting `imeVisible=true` with zero insets is documented `WindowInsets.isVisible()` semantics, not a defect. Home/spanned windows receiving IME insets must be audited by target/occlusion, **not** suppressed wholesale (spanning editors need avoidance). |
| F11 | No active WebView provider by default; WebView is Chromium 133 | Device-verified → base-image track (§13). |
| F12 | Hinge semantics: Duo 1 overlay declares `fold-[1392,0,1392,1800]` (zero-width fold), not the physical 84 px hinge; Settings splits at 1392; Launcher3 page indicator in the hinge band | Source mismatch verified; Settings causal link to be verified at runtime. **Topology-contract item in M2.1/M2.5** (real-gap feature, window-local transforms, `config_device_state_postures`), not only cosmetic. |
| F13 | Rare hinge anomaly (low readings without hall closure, then 4°→304°) | Unexplained; unreproduced in 5 trials. First add combined logging (hinge, OEM posture, hall, requested/applied composition, size/offset, timestamps). No angle filter until the cause is understood: composition is driven by OEM posture, and a jump filter can reject legitimate recovery. |
| F14 | One unexplained touch-input freeze | Watch item. Reproducible, attributable input loss is a blocker. On recurrence capture input dispatch, focused window, WM transitions and ANR evidence before rebooting. |
| F15 | Gate coverage thin | Tracked separately: G8 transition/input containment + idle redraw; gesture-origin Home (no preparatory focus tap); predictive-Back cancellation; hinge-side Back; secure-PIN G6; full repetitions. |
| F16 | Build reproducibility lessons | Done in harness + docs. |
| F17 | Unmerged upstream work overlaps ours | Integrated with authorship. Upstream coordination is useful but does **not** gate M2. |
| F18 | Nothing pushed; work on 3 local branches | Done in M2.0: tags `m0-baseline-image` and `m1-gate-image` mark the exact built commits; harness, M1 fixes and gate prototype consolidated on branch `duo-integration`, pushed to `nvillar/duo-de`. |
| F19 | No Microsoft recovery ZIP; stock partitions deleted | Open. Retained images ≠ tested rollback: record artifacts, partition/slot layout and which recovery steps were actually exercised. |
| F20 | Context-free launches land in the spanned area: LatinIME's permission activity and the runtime permission dialog opened 2784 px wide in DefaultTaskDisplayArea | Device-verified → M2.5 (context-free launch policy, §3 "Notifications and context-free launches"). |
| F21 | Security (Medium): fw 0002 added `IDisplayManager.setDisplayOffsets` reachable over Binder with **no permission check**; any app reaching the display service could shift the default display off the lit panel and out of the touch frame (device-wide availability/input impact; no data extraction or keyguard bypass shown). Source-verified by Astra review of M2.1. | Implemented (fw **0017**: `@EnforcePermission(WRITE_SECURE_SETTINGS)` on the AIDL method and the Binder entry, the same permission as the forced display size controls). Verified only by `git am -3` on a scratch `git archive` export after the full series (the series result matched the gate tree's patched HEAD for every touched path); **not yet built or device-checked**. The posture service runs as the system uid, which holds every permission (`ActivityManager.checkComponentPermission`), so behaviour should not change; device check: single-screen still engages, and an unprivileged app's call gets `SecurityException`. The M2.1 topology-owner design (under review) later removes the Binder method entirely. |

### 16.2 M2 order

Framework integration (Recents routing, parking, Home restoration, keyguard visibility, IME topology) shares ordering and visibility invariants: **one integration owner, serialized patch-series changes, one shared AOSP tree/out at a time.** Safe in parallel: source research, posture-protocol design, probe/harness/gate-tool work, geometry conformance tests, docs, input-device diagnosis, base-image package preparation (base-image changes are still accepted only on the combined image).

**M2.0 Preserve, consolidate, make testing trustworthy.**
- Tag the exact M1 gate state; consolidate harness + M1 fixes + gate prototype into one integration branch; gate report + runbook under `docs/`; push to `nvillar/duo-de` with the personal account.
- Measure warm no-op, services-only and Launcher-only build times (stable inputs, no `installclean`).
- Build the minimal trustworthy gate collector (§16.4).
- Risk-focused re-checks before substantial implementation: secure-PIN lock/wake with ordinary tasks on both panels (attempted interaction while locked); recorded Home/launch/Back transitions incl. cancellation and cross-focused gesture origin; panel-off recovery clearing both enabling properties.

**M2.2 Recents feasibility (before production parking).** On the dual topology, prove: invocation panel captured before focus changes; selection goes to that panel including cross-panel reuse; cancel preserves both workspaces; the other panel stays usable during panel-scoped Overview; Quickstep and fallback Recents layer/input correctly; animation and input leashes stay contained. Routing fix + global Overview is **not** sufficient. Timebox in build cycles; compare candidate B if it needs scattered per-handler exceptions.

**M2.1 Topology protocol + parking.** In parallel with M2.2 for design only; implement after M2.2's decision. Design: [m2.1-topology-protocol.md](m2.1-topology-protocol.md).
- Posture service stays the hardware interpreter; a **permission-protected typed Binder interface** to a system-server topology owner carries authoritative state: stable physical panel IDs and active-panel mask (independent of rotation), posture/peek state separate from task span state, initial snapshot, generation numbers, reconnect/restart handling, stale-event rejection, desired vs applied state, failure/timeout behaviour, and one path for automatic posture, manual override, wake and HAL reconnect. Define how input and content stay safe while HAL composition, touch composition and WM state change (a Binder call is not atomic across them).
- Keep standard fold semantics (DeviceState, Jetpack postures) as a derived view of the same state, not a second authority. System properties only for diagnostics/prototypes (`sys.duode.panels.active` stays as a boot capability flag). A full DeviceState-provider integration is a later alternative that needs an audit; merely adding left/right override IDs is insufficient (WM `DeviceStateController` dispatches on coarse state changes).
- **Keep the single-panel logical size for the first implementation.** A fixed 2784-wide canvas is a separate bounded experiment (1:1 rendering, active-side touch mapping, system-surface reachability, app metrics on both sides). Measure configuration callbacks/relaunches and preserve state.
- Parking: a **distinct policy-owned reason** from Home-minimize (fw 0014 clears Home's bit on move-to-front; reusing it would let launches/Recents unpark the dark side). Drive visibility/focus/lifecycle through normal WM machinery (sleep tokens are display-scoped; hiding a surface does not stop activities or exclude input). Never kill processes. Specify launches targeting a parked task, unfolding after Home, cross-panel moves, displaced tasks, process death and service/system-server restart. Guarantee task identity and restorable state, not process survival.
- Fold semantics: correct the zero-width fold declaration (F12) and window-local transforms as part of this contract.

**Then, serially:** finish Recents against real fold/parking behaviour; M2.3; M2.4.

**M2.3 Keyguard occlusion.** Preserve display-wide authentication. Permit eligible `showWhenLocked` activities without making ordinary activities on either panel visible or interactive; define the protected appearance of the other panel. Test translucent occluders, occluder dismissal/death, fold/rotation, secure unlock cancellation. Do not make "two independent keyguards" an accidental requirement; do not trade the functional failure for a disclosure.

**M2.4 IME.** Fix asymmetric editor-focus transfer. Verify input, control/layering targets, rendering, touch regions and app avoidance agree; unrelated panel tasks must not resize. Specify conservative behaviour for a keyboard covering part of a spanning window's bottom edge. Rotate/fold with the keyboard open, including a `configChanges` app.

**M2.5 Production policy closure and regression coverage.** Explicit TDA/external-display precedence; new/reused/child/context-free/notification launches (context-free launches currently land in the spanned area, F20); unsupported workspace modes (split/freeform/desktop) in panel mode; Home-minimize vs posture-park ownership; task/process restoration; minimal move/span/unspan as task transactions (polished controls stay in M3) preserving identity/back stack and the fold/re-span cancellation rule; PaneManager functional coverage (F3); panel-off behaviour unchanged.

**M2.6 Hardening + acceptance.** F13 logging, F12 cosmetic overlaps, F14 watch, full gate coverage on one integration image, Astra-reviewed.

**Base-image track (parallel authoring).** WebView default + LineageOS WebView + Jelly. No keyboard-default change (F9 withdrawn).

M2 exit: §6 M2 exit criteria + §16.2 items + full F15 coverage on a single integration image.

### 16.3 Inner development loop

Module-build/remount/sync iteration is acceptable **on this dedicated userdebug dev unit**, with coherent full images for milestone evidence. Procedure:
1. Start from a known coherent image with matching source/product/out/keys; keep the rollback image.
2. Verify root/remount and verity/overlayfs behaviour on this device; do not assume userdebug alone allows it.
3. Build the real dependent install outputs; sync matching APK/JAR/resource and optimization artifacts with a constrained source and scope (`adb sync` is non-atomic and can copy stale outputs).
4. Reboot after framework/services replacement; never hot-replace a running system_server.
5. Verify installed hashes, certificates/paths, boot completion, package/dex errors, mode properties and a focused smoke test.
6. Before gate evidence, flash a full image and prove no remount overlays or `/data` package updates shadow it.
Branch to a full image when the dependency closure is uncertain: bootclasspath changes can need matching boot-image/dexpreopt artifacts; ART/APEX changes are not ordinary JAR updates; framework resources and clients must agree; "test-keys" does not prove matching platform/shared-UID certificates.

### 16.4 Scripted gate collector requirements

- Preconditions recorded: build/component identity, both panel flags and effective mode, HOME/Recents provider, IME, navigation mode, density, physical side, logical size/offsets, secure-lock state, test-setting overrides.
- Per action: unique marker; requested target/options; task identity; parent TDA; final mode/bounds; focused/input/IME targets; lifecycle; policy-hidden reasons; bounded settling timeout.
- Real input receipt: a generated text/touch sentinel must reach the intended task and not the other (lack of a focus event is not proof).
- Restoration checks app state/back stack, not only task id.
- Controls: explicit display 0, explicit TDA, external display where available, unsupported modes, panel-off, restart/process death, `configChanges` apps.
- Transition evidence: timestamp-correlated WM/Shell/surface/input traces; final screenshots cannot prove containment.
- Fail closed: PASS / FAIL / INCOMPLETE with counts and evidence; missing logs, parser mismatch, lost connection, ambiguous identity or missing repetitions → INCOMPLETE.
- Test the tester with known wrong-panel, stale-evidence and missing-event cases; do not validate geometry only with the helper under test.
- Restore recorded settings/HOME afterwards; never log a PIN or personal editor contents.
- Must stay manual: lit/dark panel identity, physical folds and sensor timing, real hinge-edge/predictive gestures, secure-lock interaction, visual animation defects, accessibility usability, soak. The tool can guide and collect evidence for these but cannot mark them passed.

### 16.5 Superseded statements

- §5 "Do not routinely push replacement framework jars" → replaced by the controlled procedure in §16.3; full images remain required for acceptance.
- §6 M2 bullets → realised by §16.2 (M2.1–M2.5).
- §8 slices 4–6 → order is now M2.0, M2.2 feasibility, M2.1, then M2.3/M2.4, M2.5.
- §9 "make the architectural decision at M2" → provisional retention; closure via M2.2.
- §7 IME assertions → per F10 correction; add cross-focused Home, real input receipt, topology generations and interrupted restoration.
- §6 M6 "publish ... known-good artifact hashes ... alongside any candidate" and §13 "release process" → no image distribution (§18).

## 17. Astra (high) rubber-duck disposition, 2026-11-04

Reviewer: gpt-6-astra, high effort; read the plan, gate report, raw evidence, gate-branch patches and r4 sources; read-only.

| Challenge | Disposition |
|---|---|
| §15 overstated M1 ("gate done") | Accepted: reworded as exploratory slice with provisional retention and named closure criteria. |
| Recents should precede production parking | Accepted: M2.2 feasibility before M2.1 implementation; design in parallel. |
| F10 `imeVisible` misclassified; "no IME insets to spanned windows" unsafe | Accepted: corrected per `WindowInsets.isVisible()` docs; audit by target/occlusion instead. |
| Active side needs a coordinated topology protocol, not a property; same DeviceState ID for both sides | Accepted: typed Binder protocol with generations/snapshots; DeviceState derived. |
| Keep single-panel logical size for now | Accepted: fixed-width canvas is a separate experiment. |
| Parking must be a distinct reason from Home-minimize; sleep tokens are display-scoped | Accepted. |
| Production obligations dropped from §16 | Accepted: added M2.5. |
| Keyguard: "panel or display" ambiguity | Accepted: display-wide authentication preserved; M2.3 contract rewritten. |
| F12 is a topology-contract issue (zero-width fold declaration) | Accepted. |
| F13 angle filter could reject recovery | Accepted: logging first, no filter yet. |
| Gate tool must fail closed and verify real input receipt | Accepted: §16.4. |
| 1 h incremental was not a minimum; remount/sync acceptable with procedure | Accepted: measure warm builds; §16.3. |
| Upstream coordination should not gate M2 | Accepted. |

## 18. Decision: no image distribution

Owner decision (2026-11-04), verbatim: "I don't think we want to distribute an image".

- The fork (`nvillar/duo-de`) publishes source only: patches, manifests, build and test tools, and instructions. It does not publish system images, OTA feeds or other built artifacts.
- Images are built and flashed by the operator, for their own development devices, following [development-setup.md](development-setup.md).
- LineageOS's prebuilt Chromium WebView (and the Jelly source) is fetched from LineageOS by whoever builds, through the pinned manifest; this project does not redistribute it.
- M6 publishing items are replaced by operator-kept provenance and checksums (each build's `provenance.txt` and `SHA256SUMS`, and annotated tags for the harness commits of images used as evidence) and by the repository's docs (limitations, recovery instructions).
- Do not present the builds as LineageOS. Credit LineageOS for the WebView and Jelly, and keep their license notices.
