# DUO-DE development setup (Surface Duo 1)

How to set up a workstation to build, flash and test DUO-DE for the first-generation Surface Duo.

The whole flow below has been run end to end on Windows 11 with a WSL2 distribution named `duo-build`: the Podman build container, source sync, `--unshallow` (full history and the `android-16.0.0_r1`-`r3` tags), patch application, a clean vanilla build, flashing with fastboot (with `fastboot -w` coming from the release-keyed image, and without a wipe between developer images), and a `--with-probe` developer image whose DuoProbe app ran on a Duo 1.

Related: [dual-screen plan](dual-screen-plan.md), [M1 gate runbook](gate-m1.md), [M1 gate report](m1-gate-report.md).

## 1. Requirements

- Windows 11 with WSL2, or a native 64-bit Linux host.
- At least **64 GB RAM** and **400 GB free disk** for the Android checkout and build output.
- A first-generation Surface Duo with an unlocked bootloader, and a USB data cable.
- A GitHub account with a fork of `Archfx/duo-de`.

On managed Windows devices, install Python/npm/NuGet packages only from approved feeds. Use `uv` for Python tooling and **Podman** rather than Docker.

## 2. Device tools (Windows)

Install Google's platform-tools and use them from Windows for all device access:

```powershell
Invoke-WebRequest https://dl.google.com/android/repository/platform-tools-latest-windows.zip -OutFile platform-tools.zip
Expand-Archive platform-tools.zip -DestinationPath $HOME\android
$env:Path = "$HOME\android\platform-tools;$env:Path"
adb version
```

The Duo works with Windows' in-box USB drivers (`Surface Duo` for adb, `Surface Duo Fastboot` for fastboot).

Do **not** run a separate adb server inside WSL against the same device.

## 3. Repository

```powershell
git clone https://github.com/<you>/duo-de.git $HOME\Git\github\duo-de
cd $HOME\Git\github\duo-de
git remote add upstream https://github.com/Archfx/duo-de.git
git fetch upstream --tags
git config remote.pushDefault origin
```

Configure a repository-local author identity, so that a global work identity is not used:

```powershell
git config user.name "<Your Name>"
git config user.email "<id>+<github-user>@users.noreply.github.com"
```

Patches and scripts must keep **LF** line endings; `git am` and bash fail on CRLF. The repository's `.gitattributes` enforces LF for patches, scripts, makefiles and other build inputs on every platform.

If the GitHub CLI is signed in to several accounts, select the fork owner's account before pushing. An injected `GH_TOKEN` environment variable overrides `gh auth switch`.

The installed reference release is tag `v2026.10.28`.

## 4. Build environment (WSL2)

### 4.1 Linux distribution

Create a dedicated distribution, matching the repository's Ubuntu 22.04 / Java 17 baseline:

```powershell
wsl --install Ubuntu-22.04 --name duo-build --no-launch
```

Limit what WSL may use in `%UserProfile%\.wslconfig` (adjust to the machine; leave memory for Windows):

```ini
[wsl2]
memory=64GB
processors=20
swap=32GB
```

Run `wsl --shutdown` after changing it. Inside the distribution, enable systemd so that rootless Podman gets a proper cgroup v2 user session. In `/etc/wsl.conf`:

```ini
[boot]
systemd=true
```

Keep the Android source and `out/` directory on the distribution's own filesystem (for example `~/aosp`). Do **not** use `/mnt/c`: it is slower, and Windows paths lack the case sensitivity and permissions AOSP expects. Transfer changes between the Windows fork and the Linux checkout through Git, not a shared working tree.

### 4.2 Host packages

Inside the distribution:

```bash
sudo apt update
sudo apt install -y podman uidmap slirp4netns fuse-overlayfs git git-lfs repo
git lfs install
grep "^$(id -un):" /etc/subuid   # rootless Podman needs a subordinate UID range
```

Ubuntu 22.04 ships Podman 3.4; the scripts are written for it. Docker is not needed.

Rootless Podman under WSL2 needs two adjustments, otherwise the first `RUN` step of an image build fails with `crun: ... sd-bus call: Permission denied` (Podman's systemd cgroup manager has no usable user session):

```bash
sudo loginctl enable-linger "$(id -un)"
mkdir -p ~/.config/containers
cat > ~/.config/containers/containers.conf <<'EOF'
[engine]
cgroup_manager = "cgroupfs"
events_logger = "file"
EOF
```

`dev/podman.sh` also passes `--cgroup-manager=cgroupfs --events-backend=file` itself, so it works without the file; set `DUO_DEV_CGROUP_MANAGER=default` and `DUO_DEV_EVENTS_BACKEND=default` to use Podman's own settings instead. With `cgroupfs`, rootless Podman may ignore or reject resource limits such as `DUO_DEV_MEMORY`; rely on the `.wslconfig` limits instead.

### 4.3 Harness checkout

Clone your fork inside the distribution, next to (not inside) the Android tree:

```bash
git clone https://github.com/<you>/duo-de.git ~/duo-de
cd ~/duo-de
git config user.name "<Your Name>"
git config user.email "<id>+<github-user>@users.noreply.github.com"
```

The developer scripts live in `dev/`; `build/dev/pinned.xml` is the pinned developer manifest.

Development happens on the `duo-integration` branch (`git checkout duo-integration`); `main-16` tracks upstream. Annotated tags mark the exact harness commits of images that were built and tested on the device:

| Tag | Image | Build options |
|---|---|---|
| `m0-baseline-image` | `duodev.20261104183202.0b049383.baseline` (unmodified series) | `--label baseline` |
| `m1-gate-image` | `duodev.20261105003423.bfb0d8b7.m1gate` (M1 gate prototype) | `--with-probe --label m1gate --reset-patches --unshallow` |

`git show <tag>` prints the image's SHA-256. Rebuilding a tag gives a new build id (it contains the build time), so the image checksum differs from the recorded one.

### 4.4 Build container

```bash
~/duo-de/dev/podman.sh image
```

This builds `localhost/duo-de-build:22.04` from `dev/Containerfile` (Ubuntu 22.04, OpenJDK 17, repo and git-lfs from the Ubuntu archive): about 1.4 GB, about 90 s. Re-run it to pick up base-image updates; the image id is recorded in each build's provenance.

The image's `repo` launcher (Ubuntu's, 2.17) is older than the one `repo init` places in the tree. The scripts run the tree's own launcher (`.repo/repo/repo`) when it exists; with the system launcher, repo only prints a harmless version warning.

## 5. Developer build

The developer flow builds only the vanilla `treble_arm64_bvN-bp2a-userdebug` system image. It never uploads, never uses the upstream release keys, and only writes inside the Android tree and the artifacts directory.

### Long-running commands under WSL

WSL shuts a distribution down shortly after the last attached `wsl.exe` client exits, even if Linux processes (a sync or build) are still running, and even with systemd enabled. Run long syncs and builds from a terminal that stays attached: open `wsl -d duo-build`, start `tmux` (or `screen`) inside it, run the command there, and keep that Windows terminal window open.

Microsoft documents the delay as `instanceIdleTimeout` in the `[general]` section of `%UserProfile%\.wslconfig` (milliseconds, default 15000; `-1` disables automatic shutdown), see [Advanced settings configuration in WSL](https://learn.microsoft.com/windows/wsl/wsl-config#general-wsl-settings). Changing it is optional with an attached terminal; set `instanceIdleTimeout=-1` if you rely on builds running without one.

Keep Windows awake during long builds: sleep or hibernation suspends the WSL VM. Adjust the Windows power settings, or keep a process running that holds `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`.

An interrupted build usually resumes incrementally: rerun the same `dev/podman.sh build` command **without** `--reset-patches`. The recorded patches are kept and the build system reuses `out/`. If the WSL instance stopped or was suspended mid-build, some intermediate files may be truncated but still look up to date to Ninja; if the rebuild then fails with errors like those in 5.8, remove `out/` and build clean. An interrupted sync is resumed by rerunning `dev/podman.sh sync`; afterwards the build's checkout check (see 5.8) catches half-written projects.

### 5.1 Sync the source

```bash
~/duo-de/dev/podman.sh sync ~/aosp      # or, on the host: ~/duo-de/dev/sync.sh ~/aosp
```

`dev/sync.sh`:

- runs `repo init` for `android-16.0.0_r4` with `--git-lfs --depth=1` if the tree is new (`--depth 0` for full history);
- installs `build/dev/pinned.xml` and `build/remove.xml` as local manifests, moving any other local manifest to `.repo/local_manifests.disabled/`;
- runs `repo sync -c --no-clone-bundle --no-tags --retry-fetches=5` with 4 parallel jobs. It does not `git clean`, reset or force-sync.

android.googlesource.com rate-limits heavy clients with **HTTP 429**; a first sync at high concurrency (for example `-j16`) can hit it. `dev/sync.sh` therefore defaults to `--jobs 4`, retries each fetch (`--retry-fetches 5`) and re-runs a failed sync up to `--attempts 4` times, pausing 60 s, 120 s, 240 s in between. If it still fails, wait a while and rerun with `--jobs 2`; finished projects are not fetched again.

`dev/sync.sh` and `dev/build.sh` take the same lock (`<tree>/.duo-dev/lock`); either refuses to start while the other is running on the tree.

The pinned manifest omits the GApps project. Add `--with-gapps` to include `vendor/gapps` (also pinned).

**WebView and Jelly (LineageOS).** Both manifests remove AOSP's `platform/external/chromium-webview` (Chromium 133 for `bp2a`) and add, pinned, LineageOS's `android_external_chromium-webview_patches` (its `os_pickup.bp` and `README` are linked to `external/chromium-webview/Android.bp` and `README`), `android_external_chromium-webview_prebuilt_arm64` (`webview.apk`, Git LFS, about 250 MB, downloaded from `review.lineageos.org`) and `android_packages_apps_Jelly`. Only the arm64 prebuilt is synced: `android_app_import` takes the first device architecture, so arm64 products need nothing else; 32-bit products would also need `..._prebuilt_arm`. On an existing tree, `repo sync` deletes the old `external/chromium-webview` checkout as an obsolete project; if it refuses, move that directory out of the tree and sync again. Repo keeps the old project's Git data in `.repo/` (its `DeleteWorktree` leaves the cache in place, so that a manifest change can be reverted without a new download): `.repo/project-objects/platform/external/chromium-webview.git` (36 GB on the r4 tree) and the small `.repo/projects/external/chromium-webview.git`. To reclaim the space once nothing needs the old WebView, a one-time cleanup is to delete both directories together; syncing an older harness commit (for example a tag from before this change) then downloads the project again.

The LineageOS WebView is Chromium built with LineageOS's settings, which include `ffmpeg_branding = "Chrome"` and `proprietary_codecs = true` (H.264, AAC and other proprietary codecs; see the pinned patches project's `README`). Chromium's guide for system integrators notes that these codecs may be covered by patents or licensing agreements and advises seeking legal advice before distributing a WebView that includes them ([Proprietary codecs](https://chromium.googlesource.com/chromium/src/+/HEAD/android_webview/docs/aosp-system-integration.md#proprietary-codecs)). This project does not distribute images or the WebView: whoever builds an image fetches the WebView from LineageOS through the manifest (see [the decision in the plan](dual-screen-plan.md#18-decision-no-image-distribution)).

**History needed by the patches.** A `--depth=1` tree is fine for building, but `git am -3` needs the blobs each patch was made against, and fails with `sha1 information is lacking or useless ... could not build fake ancestor` when they are missing. The patched projects (about 37) therefore need:

- **full history**, not a shallow clone. `dev/build.sh` checks this before applying patches and stops with a list of shallow projects;
- for AOSP-hosted projects (remote `aosp`), the **earlier release tags** `android-16.0.0_r1` to `r3`. Several patches were generated on r2-era sources; their base blobs are reachable only from those tags, not from the r4 branch. GitHub-hosted projects and projects without such tags are not affected.

Run the first patch step with `--unshallow`, which fetches both, from each project's own remote, under the tree lock, with retries and growing pauses for HTTP 429:

```bash
~/duo-de/dev/podman.sh build ~/aosp --unshallow --patch-only
```

It fetches the full history of those projects only, which includes large ones such as `frameworks/base` and `frameworks/av`: expect several GB more disk and tens of minutes, depending on bandwidth and rate limiting. Tags are fetched with the refspec `+refs/tags/android-16.0.0_r*:refs/tags/android-16.0.0_r*`, and each project is recorded in `~/aosp/.duo-dev/base-tags-fetched` so later runs skip it. Without `--unshallow`, missing tags are only a warning. If a patch still fails after this, its base is not in the project's history and the patch must be regenerated (see 5.5).
### 5.2 Build

```bash
~/duo-de/dev/podman.sh build ~/aosp
```

Build times measured on a 20-thread host with 64 GB assigned to WSL, each producing a full system image:

| Build | Time |
|---|---|
| Clean build (empty `out/`) | 5 h 49 min, about 188k Ninja steps |
| Rebuild with new framework/Launcher3 patches, default `installclean`, new build number | 1 h 07 min, about 22k steps (about 25 min of Soong analysis) |
| Warm tree, `--with-probe --no-installclean`, no source change | 2 min 24 s, 112 steps |
| Same, after touching sources only: `frameworks/base/services` / Launcher3 | 2 min 08 s / 3 min 45 s |
| Same, one added constant in a window-manager class in `services` | 3 min 17 s, 227 steps (Ninja 2 min 42 s) |

For quick iterations, use `--no-installclean` with otherwise unchanged options. It skips `make installclean`, so files of modules that are no longer built can stay in `out/` and end up in the image; the one case the build detects is a DuoProbe left over from a `--with-probe` build, which stops a later build without `--with-probe`. Use the default (`installclean`) for images that serve as milestone or gate evidence.

`dev/build.sh` (run inside the container by `dev/podman.sh`):

1. Applies the `trebledroid`, `personal` and `duo` patch sets in that order through the repository's `patch.sh` (`git am -3`). It records the result in `~/aosp/.duo-dev/patch-state`; later runs skip patching while the patches and project heads are unchanged, and keep uncommitted edits in the tree. If the patches change, patched projects are moved back to their manifest revision and the patches re-applied. If a fetch-only `repo sync` moved a project's manifest revision, the patches are re-applied on the new revision. A patch that fails to apply stops the build.
2. Writes `device/phh/treble/duo-dev.mk` from `build/aosp.mk`, with the updater feed `ro.system.ota.json_url` replaced, and generates the product makefiles from it.
3. Rebuilds TrebleApp (`treble_app`, Gradle; needs network access on first run) and installs it as `vendor/hardware_overlay/TrebleApp/app.apk`, as `build.sh` does.
4. Checks the WebView source: `external/chromium-webview` is LineageOS's layout (not the AOSP project), and `prebuilt/arm64/webview.apk` has the SHA-256 of its Git LFS object (not an undownloaded pointer). Then builds `systemimage` with `BUILD_NUMBER` set to a unique build id, for example `duodev.20261104081530.b43214d2`. The id appears in the build fingerprint.
5. Verifies the AOSP test keys (`test-keys`), the updater feed and the fingerprint; that the built WebView is `com.android.webview` with the Chromium version from the LineageOS `README`, and that Jelly and `DuoVanillaWebViewOverlay` were built; and that the patched and pinned source projects (commits, tracked edits, untracked non-ignored files), the generated product makefiles, TrebleApp and the local manifests did not change during the build, then writes the artifacts.

Results go to `~/aosp/duo-dev-artifacts/<build-id>/`:

| File | Content |
|---|---|
| `system-treble_arm64_bvN-<build-id>.img` | the system image |
| `…img.sha256` | its SHA-256 |
| `provenance.txt` | build id, fingerprint, harness commit, patch-set hash, pinned-manifest check, projects with uncommitted changes, WebView (prebuilt commit, Chromium version, source and installed APK SHA-256, provider overlay), Jelly APK SHA-256, container image id, toolchain versions |
| `manifest.xml` | `repo manifest -r` of the tree that was built (patched projects show their post-patch HEADs) |
| `pinned-bases.txt` | per pinned project: pinned revision, the revision compared with it (for patched projects the base the patches were applied on, otherwise the checked-out HEAD), repo's current `m/` ref (diagnostic only) and the built HEAD |
| `patch-state.txt`, `system-build.prop` | applied patch state and the image's build properties |
| `SHA256SUMS` | checksums of all of the above |

Useful options (`dev/build.sh --help`):

| Option | Effect |
|---|---|
| `--label TEXT` | append a label to the build id |
| `--ota-url URL` | updater feed to bake in (https only; Archfx URLs are refused) |
| `--reset-patches` | abort a stuck `git am`, force patched projects back to their manifest revision (discarding tracked changes in them) and re-apply all patches |
| `--patch-only` | apply patches and generate makefiles, then stop |
| `--repair-checkouts` | `git reset --hard HEAD` broken checkouts in unpatched projects without local commits (see 5.8) |
| `--unshallow` | fetch full history and the `android-16.0.0_r*` tags for patched projects before applying patches |
| `--skip-treble-app` | keep the TrebleApp already in `vendor/hardware_overlay` |
| `--with-probe` | dev images only: copy `tools/duo-probe/DuoProbe` to `packages/apps/DuoProbe` (with a `.duo-dev-managed` marker) and add `PRODUCT_PACKAGES += DuoProbe` and `ro.duode.dev_probe=true` to the untracked `duo-dev.mk`. Recorded as `duo_probe` in `provenance.txt`. A later build without it removes the copy and the makefile lines; an unmarked `packages/apps/DuoProbe` is refused, never overwritten. Release makefiles (`build/aosp.mk`) never name the probe |
| `--no-installclean` | skip `make installclean` for faster incremental builds |
| `--artifacts-dir DIR` | write results elsewhere |

Container settings are environment variables of `dev/podman.sh`: `DUO_DEV_MEMORY` (for example `48g`) sets a memory limit, `DUO_DEV_CPUS` a CPU limit and the default job count. A CPU limit needs the `cpu` cgroup controller delegated to the user, which a default WSL/systemd setup does not do; leave it unset there. `dev/podman.sh shell ~/aosp` opens a shell in the container.

### 5.3 Updater feed and signing

Developer builds set `ro.system.ota.json_url` to `https://ota-disabled.invalid/duo-de-dev/ota.json`, a reserved domain that never resolves, so the in-ROM updater can never install an upstream OTA over a developer build. `ro.duode.dev_build=true` marks such images.

They are signed with the public AOSP test keys. Release images are signed with the upstream maintainer's private keys, which are not available. Moving between a release image and a developer image therefore needs a userdata wipe (see 6). Developer images share the test keys; flashing one over another keeps userdata.

### 5.4 Refreshing the pinned manifest

`build/dev/pinned.xml` pins every moving branch of `build/default.xml` to a commit, resolved with `git ls-remote`. The pins are not proven to be the exact inputs of release `v2026.10.28`. To update one, resolve the branch named in its `upstream` attribute and replace `revision`:

```bash
git ls-remote https://github.com/Archfx/duoPosture refs/heads/main
```

Keep the resolution date in the file's header current, re-run `dev/podman.sh sync`, then build.

The three LineageOS projects (WebView patches, WebView arm64 prebuilt, Jelly) are pinned in `build/default.xml` too, so release builds use the same commits. Before each image build, check for a newer WebView and move the patches and prebuilt pins **together** (the prebuilt's `<uses-library>` entries must match `os_pickup.bp`), in both files:

```bash
git ls-remote https://github.com/LineageOS/android_external_chromium-webview_patches refs/heads/main
git ls-remote https://github.com/LineageOS/android_external_chromium-webview_prebuilt_arm64 refs/heads/main
git ls-remote https://github.com/LineageOS/android_packages_apps_Jelly refs/heads/lineage-23.2
```

Update the Chromium version and LFS object id in the comment in `build/dev/pinned.xml` (`git -C external/chromium-webview/prebuilt/arm64 cat-file blob HEAD:webview.apk` prints the pointer). Move Jelly only when its new commit still builds against the r4 prebuilts (androidx, Kotlin) in the tree.

### 5.5 Checking that the patch series applies

After changing patches (for example after a TrebleDroid sync, or when moving to a new AOSP tag), check the whole series against a clean tree. There is no `dev/podman.sh` subcommand for it; open a container shell on the tree and run the script there. Inside the container `HOME` is `<tree>/.duo-dev/home`, so `~` does not point at your home directory: use absolute paths (the tree and the harness are mounted at their host paths, and the working directory is the tree):

```bash
~/duo-de/dev/podman.sh shell ~/aosp                                  # on the host
/home/<user>/duo-de/dev/check-apply.sh --unshallow --restore "$PWD"  # inside the container
exit
```

`dev/check-apply.sh` force-checks-out every patched project at its manifest revision (discarding tracked changes and patch commits in those projects only, like `--reset-patches`), then applies the `trebledroid`, `personal` and `duo` sets in upstream order with `git am -3`. It does not stop at the first failure: a failing patch is aborted and reported, the rest of that project's patches are skipped, and the other projects continue. It ends with a summary and exits 1 if anything failed. Always pass `--restore`: it returns the patched projects to their manifest revision afterwards (also after failures), and `dev/build.sh` re-applies the patches itself on its next run. Without it the projects are left at patch commits that `dev/build.sh` may not recognise as its recorded patched state; it then refuses to build until rerun with `--reset-patches`.

To fix a failing patch, apply it by hand in that project (`git am -3`, resolve, `git am --continue`, or re-create the change), regenerate it with `git format-patch -1` into the same file name, and re-run the check.

### 5.6 Testing the developer scripts

After changing anything in `dev/`, run the mock tests. They use small synthetic Git repositories and stubbed `repo`/`lunch`/`make` in a temporary directory, and need no Android tree, network or container:

```bash
~/duo-de/dev/tests/run.sh
```

### 5.7 Copying the image to Windows

```bash
cp ~/aosp/duo-dev-artifacts/<build-id>/system-*.img* /mnt/c/Users/<you>/Downloads/
```

Verify the checksum on Windows before flashing:

```powershell
(Get-FileHash .\system-treble_arm64_bvN-<build-id>.img -Algorithm SHA256).Hash
```

### 5.8 Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `repo sync` fails with HTTP 429 | Rate limiting by android.googlesource.com. Wait, then rerun `dev/sync.sh` with `--jobs 2` (see 5.1). |
| `dev/build.sh` stops: *projects have staged changes or a broken checkout*; or Soong fails with undefined modules such as `python-symbol` or `platform_tools_version` | An interrupted `repo sync` checkout left every tracked file of a project staged for deletion (seen in `development` and `developers/build`). Before patching, `dev/build.sh` checks that every project that is not a patch target has an index equal to its HEAD (one `git diff --cached --quiet` per project from `.repo/project.list`), and lists those that do not. Repair each with `git -C ~/aosp/<project> reset --hard HEAD`, or rerun with `--repair-checkouts`, which does this only for projects whose HEAD is still the manifest revision (no local commits). Patch targets are handled by the patch step (`--reset-patches`). |
| `sha1 information is lacking or useless ... could not build fake ancestor` | Missing history or base tags in a patched project: rerun with `--reset-patches --unshallow` (see 5.1). |
| A patch set fails to apply for another reason | Run `dev/check-apply.sh` to list every failing patch, then regenerate them (see 5.5). |
| `crun: ... sd-bus call: Permission denied` | Rootless Podman without a systemd user session; see 4.2. |
| The distribution stopped in the middle of a sync or build | The `wsl.exe` client that started it exited, and WSL idled out the distribution. Run long commands from an attached terminal with `tmux`/`screen` (see "Long-running commands under WSL" above), then rerun the same command without `--reset-patches` to continue incrementally. |
| After a WSL stop or Windows sleep mid-build: `no element found` for an empty `AndroidManifest.xml`, `failed to open APK: Invalid file`, then Kotlin `unresolved reference` or `cannot access kotlin.String` | Truncated or corrupt intermediate files that Ninja considers up to date. Deleting individual suspect outputs was not enough (it failed even after all 11,786 archives validated OK); remove `~/aosp/out` and rebuild clean. Keep the host awake to avoid this (see "Long-running commands under WSL"). |
| *a dev/build.sh or dev/sync.sh is running on the tree* | Another developer script holds `<tree>/.duo-dev/lock`. Wait for it to finish. |
| *webview.apk does not match its Git LFS object* | The LFS download failed and the file is the pointer text. Run `git -C ~/aosp/external/chromium-webview/prebuilt/arm64 lfs pull` (needs `review.lineageos.org`). |
| *external/chromium-webview is still AOSP's ... project* | The tree was synced before the LineageOS WebView change. Re-run `dev/sync.sh` (see 5.1). |

## 6. Flashing a system image

From Windows, with the Duo booted to Android. Between developer images (same AOSP test keys), flash without a wipe; data, settings and persisted properties are kept:

```powershell
adb reboot fastboot
fastboot getvar is-userspace     # must be: yes
fastboot getvar current-slot     # a or b
fastboot flash system_<slot> <image>.img
fastboot reboot
```

From a differently signed image (an upstream release-keyed DUO-DE image to a test-keyed developer image, or back), wipe userdata in the same fastboot session, after flashing and before rebooting:

```powershell
fastboot flash system_<slot> <image>.img
fastboot -w
fastboot reboot
```

On the Duo, `fastboot -w` prints `Erasing 'metadata' OKAY` and then `Erase successful, but not automatically formatting. File system type raw not supported.`; that message is expected. Android resets userdata itself on the first boot (about 35 s extra). Afterwards, accept the USB debugging prompt on the device again: the wipe removes the stored adb authorization.

Fallback: if a boot after a signing change instead stops in recovery (for example *Can't load Android system*), choose **Factory data reset** with the volume keys, confirm with Power, then **Reboot system now**. Moving from stock Android 12L follows the README's flashing steps.

Keep the bootloader **unlocked** while running custom images; relocking can prevent the device from booting.

### Known-good fallback

Keep the upstream release image and verify it before use:

- Release image: `aosp-arm64-ab-vanilla-16.0-20261028.img.xz`
- SHA-256: `1b8cf8896b067b1685e51131dc80cb25804595be3027bff580d84d6fc2a1a587`

Returning to Microsoft's stock firmware requires the official Surface Duo recovery ZIP, and the README's return-to-stock procedure. Installing DUO-DE deletes the stock `system_ext`, `product` and inactive `system` partitions.

## 7. Testing on the device

Wake and unlock (without a secure lock screen), and keep the screen on while connected:

```powershell
adb shell input keyevent KEYCODE_WAKEUP
adb shell wm dismiss-keyguard
adb shell settings put global stay_on_while_plugged_in 7   # restore to 0 afterwards
```

Find UI element bounds for scripted taps:

```powershell
adb shell uiautomator dump /data/local/tmp/ui.xml
adb shell cat /data/local/tmp/ui.xml
```

Capture screenshots on the device and pull them. In Windows PowerShell 5, redirecting `adb exec-out screencap` output to a file corrupts the PNG.

```powershell
adb shell screencap -p /data/local/tmp/s.png
adb pull /data/local/tmp/s.png
```

Useful state dumps: `dumpsys activity containers`, `dumpsys window`, `dumpsys input`, `dumpsys display`, `dumpsys device_state`.

Check which build is running: `adb shell getprop ro.system.build.fingerprint` (developer builds contain `duodev.`) and `adb shell getprop ro.system.ota.json_url`.

### WebView and browser (vanilla)

On a vanilla build, `com.android.webview` (LineageOS Chromium) is the default WebView provider and Jelly is the browser:

```powershell
adb shell dumpsys webviewupdate                     # Current WebView package: (com.android.webview, <Chromium version>)
adb shell cmd overlay list android | Select-String webview   # [x] dev.duode.overlay.vanilla.webview
adb shell pm list packages org.lineageos.jelly
adb shell am start -a android.intent.action.VIEW -d https://example.com   # opens Jelly
```

A device that was set up before this change keeps a WebView provider chosen in Developer options while it stays valid; without one, the overlay's default applies from the next boot.

### Experimental two-panel mode

Two-panel mode is read once at boot:

```powershell
adb shell setprop persist.debug.phh.duo.panels 1; adb reboot   # enable
adb shell setprop persist.debug.phh.duo.panels 0; adb reboot   # disable
```

`persist.sys.phh.duo.panels` also enables it; reset whichever is set.

These properties persist in userdata. They survive reflashing the system image, but a factory reset clears them.
