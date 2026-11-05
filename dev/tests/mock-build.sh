#!/bin/bash
# Stubbed full run of dev/build.sh (fake lunch/make/repo) plus sync/build locking.
# Runs after mock-patches.sh in the same DUO_TEST_DIR; run via dev/tests/run.sh.
# shellcheck disable=SC2015,SC2012
set -euo pipefail
T="${DUO_TEST_DIR:?}"
H="$T/harness"
cat > "$T/bin/repo" <<'EOF'
#!/bin/bash
if [ "$1" == manifest ]; then
  out="${@: -1}"
  cat > "$out" <<X
<manifest><project path="device/phh/treble" name="TrebleDroid/device_phh_treble" revision="ad694645eb591f00b3acced10b546397812435ec"/></manifest>
X
else echo "repo version v2.99 (stub)"; fi
EOF
chmod +x "$T/bin/repo"; export PATH="$T/bin:$PATH"
cat > "$T/tree/build/envsetup.sh" <<'EOF'
lunch() { export OUT="$PWD/out/target/product/tdgsi_arm64_ab"; echo "lunch $1"; }
get_build_var() { echo ""; }
make() {
  echo "make $*"
  case "$*" in *systemimage*)
    mkdir -p "$OUT/system"
    printf 'ro.system.build.fingerprint=google/treble_arm64_bvN/tdgsi_arm64_ab:16/BP2A/%s:userdebug/test-keys\nro.system.build.tags=test-keys\nro.system.ota.json_url=%s\n' "$BUILD_NUMBER" "${FAKE_URL:-https://ota-disabled.invalid/duo-de-dev/ota.json}" > "$OUT/system/build.prop"
    head -c 1048576 /dev/urandom > "$OUT/system.img"
    mkdir -p "$OUT/system/product/app/webview" "$OUT/system/product/app/Jelly" "$OUT/system/product/overlay"
    echo resigned > "$OUT/system/product/app/webview/webview.apk"; echo jelly > "$OUT/system/product/app/Jelly/Jelly.apk"
    echo rro > "$OUT/system/product/overlay/DuoVanillaWebViewOverlay.apk"
    # Like installclean + PRODUCT_PACKAGES: the probe apk exists only when duo-dev.mk names it.
    if grep -q '^PRODUCT_PACKAGES += DuoProbe' device/phh/treble/duo-dev.mk 2>/dev/null; then
      mkdir -p "$OUT/system/app/DuoProbe"; echo apk > "$OUT/system/app/DuoProbe/DuoProbe.apk"
    else rm -rf "$OUT/system/app/DuoProbe"; fi;;
  esac
}
EOF
# LineageOS WebView layout (build/dev/pinned.xml): patches project + linkfiles, LFS prebuilt.
W="$T/tree/external/chromium-webview"
mkdir -p "$W/patches" "$W/prebuilt/arm64" "$T/tree/prebuilts/sdk/tools/linux/bin"
echo 'android_app_import { name: "webview", arch: { arm64: { apk: "prebuilt/arm64/webview.apk" } } }' > "$W/patches/os_pickup.bp"
echo 'android_default_version_name = "156.0.8078.47"' > "$W/patches/README"
git -C "$W/patches" init -q; git -C "$W/patches" add -A; git -C "$W/patches" commit -qm webview
ln -s patches/os_pickup.bp "$W/Android.bp"; ln -s patches/README "$W/README"
head -c 4096 /dev/urandom > "$T/webview.apk"
printf 'version https://git-lfs.github.com/spec/v1\noid sha256:%s\nsize 4096\n' "$(sha256sum "$T/webview.apk" | cut -d' ' -f1)" > "$T/webview.ptr"
cp "$T/webview.ptr" "$W/prebuilt/arm64/webview.apk"
git -C "$W/prebuilt/arm64" init -q; git -C "$W/prebuilt/arm64" add -A; git -C "$W/prebuilt/arm64" commit -qm prebuilt
# As the LFS smudge filter would: real content in the work tree, clean status.
cp "$T/webview.apk" "$W/prebuilt/arm64/webview.apk"; git -C "$W/prebuilt/arm64" update-index --assume-unchanged webview.apk
cat > "$T/tree/prebuilts/sdk/tools/linux/bin/aapt2" <<'EOF'
#!/bin/bash
case "$1 $2" in
  "dump badging") echo "package: name='com.android.webview' versionCode='807805050' versionName='${FAKE_WV_VERSION:-156.0.8078.47}' platformBuildVersionName=''" ;;
  "dump xmltree") printf '%s\n' 'N: android=http://schemas.android.com/apk/res/android' 'E: webviewproviders (line=12)' '  E: webviewprovider (line=13)' '    A: packageName="com.android.webview" (Raw: "com.android.webview")' ;;
esac
EOF
chmod +x "$T/tree/prebuilts/sdk/tools/linux/bin/aapt2"
mkdir -p "$T/tree/vendor/hardware_overlay/TrebleApp"; echo apk > "$T/tree/vendor/hardware_overlay/TrebleApp/app.apk"
mkdir -p "$T/tree/.repo/local_manifests"; cp "$H/build/dev/pinned.xml" "$T/tree/.repo/local_manifests/duo-de-pinned.xml"
bash "$H/dev/build.sh" --skip-treble-app --label mock --jobs 2 "$T/tree"
A=$(ls -d "$T/tree/duo-dev-artifacts/"*mock); ls -la "$A"; cat "$A/provenance.txt"; (cd "$A" && sha256sum -c SHA256SUMS)
grep -q "^webview_chromium_version: 156.0.8078.47 " "$A/provenance.txt" \
    && grep -q "^webview_source_apk_sha256: $(sha256sum "$T/webview.apk" | cut -d' ' -f1) " "$A/provenance.txt" \
    && grep -q '^webview_default_provider_overlay: .*first provider com.android.webview$' "$A/provenance.txt" \
    && grep -q '^jelly_apk_sha256: [0-9a-f]\{64\}$' "$A/provenance.txt" \
    && echo "PASS provenance records WebView, its overlay and Jelly" || echo "FAIL WebView/Jelly provenance"
echo "---- WebView source checks"
webview_refused() { # label expected-message
    if bash "$H/dev/build.sh" --skip-treble-app --label "$1" "$T/tree" >/dev/null 2>"$T/wv.err"; then echo "FAIL $1 accepted"
    elif grep -q "$2" "$T/wv.err" && ! ls -d "$T/tree/duo-dev-artifacts/"*"$1" >/dev/null 2>&1; then echo "PASS $1 refused"
    else echo "FAIL $1"; cat "$T/wv.err"; fi
}
cp "$T/webview.ptr" "$W/prebuilt/arm64/webview.apk"
webview_refused lfspointer "lfs pull"
cp "$T/webview.apk" "$W/prebuilt/arm64/webview.apk"
mkdir "$W/.git"; webview_refused aospwebview "still AOSP's platform/external/chromium-webview"; rmdir "$W/.git"
export FAKE_WV_VERSION=133.0.6943.137; webview_refused wvversion "versionName is '133.0.6943.137'"; unset FAKE_WV_VERSION
echo "---- bad url in image must fail"
if FAKE_URL=https://raw.githubusercontent.com/Archfx/duo-de/main-16/config/ota.json bash "$H/dev/build.sh" --skip-treble-app --label bad "$T/tree"; then echo FAIL; else echo "PASS rejects upstream url"; fi
if bash "$H/dev/build.sh" --ota-url https://raw.githubusercontent.com/Archfx/x/ota.json "$T/tree"; then echo FAIL; else echo "PASS rejects archfx --ota-url"; fi
B=$(git -C "$T/tree/frameworks/base" rev-parse refs/remotes/m/android-16.0.0_r4)
M=$(git -C "$T/tree/build/make" rev-parse refs/remotes/m/android-16.0.0_r4)
# Regression (review #3): pins compare against pre-patch bases, not patched HEADs.
cat > "$H/build/dev/pinned.xml" <<X
<manifest><project path="frameworks/base" name="fb" revision="$B"/><project path="build/make" name="bm" revision="$M"/></manifest>
X
bash "$H/dev/build.sh" --skip-treble-app --label pins "$T/tree" >/dev/null 2>&1
A=$(ls -d "$T/tree/duo-dev-artifacts/"*pins)
grep -q '^pinned_manifest_check: match' "$A/provenance.txt" && echo "PASS pins match on patched tree" || { echo FAIL; grep pinned_manifest "$A/provenance.txt"; }
[ "$(git -C "$T/tree/frameworks/base" rev-parse HEAD)" != "$B" ] && echo "PASS (frameworks/base really is patched)"
grep frameworks/base "$A/pinned-bases.txt"
sed -i "s#name=\"bm\" revision=\"[0-9a-f]*\"#name=\"bm\" revision=\"0000000000000000000000000000000000000000\"#" "$H/build/dev/pinned.xml"
bash "$H/dev/build.sh" --skip-treble-app --label pinbad "$T/tree" >/dev/null 2>&1
grep -q '^pinned_manifest_check: MISMATCH (1 projects): build/make' "$(ls -d "$T/tree/duo-dev-artifacts/"*pinbad)/provenance.txt" && echo "PASS real pin mismatch reported"
# Regression (re-review #1): unpatched projects are checked by HEAD, not by the m/ ref.
O=$(git -C "$T/tree/vendor/hardware_overlay" rev-parse refs/remotes/m/android-16.0.0_r4)
cat > "$H/build/dev/pinned.xml" <<X
<manifest><project path="frameworks/base" name="fb" revision="$B"/><project path="build/make" name="bm" revision="$M"/><project path="vendor/hardware_overlay" name="ho" revision="$O"/></manifest>
X
pincheck() { grep '^pinned_manifest_check:' "$(ls -d "$T/tree/duo-dev-artifacts/"*"$1")/provenance.txt"; }
bash "$H/dev/build.sh" --skip-treble-app --label pinok "$T/tree" >/dev/null 2>&1
pincheck pinok | grep -q ': match' && echo "PASS pins match incl. unpatched project"
git -C "$T/tree/vendor/hardware_overlay" commit -q --allow-empty -m "other checkout"
bash "$H/dev/build.sh" --skip-treble-app --label pinhead "$T/tree" >/dev/null 2>&1
pincheck pinhead | grep -q 'MISMATCH (1 projects): vendor/hardware_overlay' && echo "PASS unpatched checkout differing from pin (m/ unchanged) reported" || { echo FAIL; pincheck pinhead; }
git -C "$T/tree/vendor/hardware_overlay" checkout -q --detach "$O"
# Regression (re-review #1): a fetch-only m/ move invalidates the fast path; the recorded base is checked.
X1=$(git -C "$T/tree/frameworks/base" commit-tree "$B^{tree}" -p "$B" -m "upstream advance")
git -C "$T/tree/frameworks/base" update-ref refs/remotes/m/android-16.0.0_r4 "$X1"
out=$(bash "$H/dev/build.sh" --patch-only "$T/tree" 2>&1)
if grep -q "already applied" <<<"$out"; then echo "FAIL fast path used after m/ moved"; else echo "PASS m/ move invalidates fast path"; fi
[ "$(awk '$2 == "frameworks/base" {print $3}' "$T/tree/.duo-dev/patch-state")" == "$X1" ] && echo "PASS patches re-applied on the new base"
bash "$H/dev/build.sh" --skip-treble-app --label pinmoved "$T/tree" >/dev/null 2>&1
pincheck pinmoved | grep -q 'MISMATCH (1 projects): frameworks/base' && echo "PASS recorded base differing from pin reported" || { echo FAIL; pincheck pinmoved; }
git -C "$T/tree/frameworks/base" update-ref refs/remotes/m/android-16.0.0_r4 "$B"
bash "$H/dev/build.sh" --patch-only "$T/tree" >/dev/null 2>&1
# Regression (review #1): shared lock.
mkdir -p "$T/tree/.duo-dev"; cp "$T/tree/.repo/local_manifests/duo-de-pinned.xml" "$T/lm.before"
( flock 8; sleep 8 ) 8>"$T/tree/.duo-dev/lock" & sleep 1
if bash "$H/dev/sync.sh" --no-sync "$T/tree" 2>/dev/null; then echo "FAIL sync ran under lock"; else echo "PASS sync refuses while locked"; fi
cmp -s "$T/lm.before" "$T/tree/.repo/local_manifests/duo-de-pinned.xml" && echo "PASS manifests untouched under lock"
if bash "$H/dev/build.sh" --skip-treble-app "$T/tree" 2>/dev/null; then echo "FAIL build ran under lock"; else echo "PASS build refuses while locked"; fi
wait
bash "$H/dev/sync.sh" --no-sync "$T/tree" 2>/dev/null && echo "PASS sync --no-sync runs when unlocked"
# Regression (reviews): tree changed mid-build -> no artifacts; ignored outputs are fine.
cat >> "$T/tree/build/envsetup.sh" <<'X'
if [ -n "${MUTATE:-}" ]; then eval "orig_$(declare -f make)"; make() { orig_make "$@"; case "$*" in *systemimage*)
  case "$MUTATE" in
    patched) git -C frameworks/base -c user.name=x -c user.email=x@x commit -q --allow-empty -m concurrent ;;
    unpatched) echo changed >> vendor/hardware_overlay/file.txt ;;
    untracked) echo new > vendor/hardware_overlay/newsrc.c ;;
    genmk) echo "# changed" >> device/phh/treble/treble_arm64_bvN.mk ;;
    ignored) echo out > vendor/hardware_overlay/output.o ;;
  esac ;; esac; }; fi
X
echo '*.o' >> "$T/tree/vendor/hardware_overlay/.git/info/exclude"
mutate() { # kind expect(reject|accept)
    if MUTATE=$1 bash "$H/dev/build.sh" --skip-treble-app --label "mut$1" "$T/tree" 2>"$T/mut.err" >/dev/null; then r=accept; else r=reject; fi
    arts=$(ls -d "$T/tree/duo-dev-artifacts/"*"mut$1" 2>/dev/null || true)
    if [ "$r" == "$2" ] && { [ "$2" == accept ] || { [ -z "$arts" ] && grep -q "changed during the build" "$T/mut.err"; }; }; then
        echo "PASS mid-build change '$1' -> $2"
    else
        echo "FAIL mid-build change '$1': got $r"; cat "$T/mut.err"
    fi
}
mutate ignored accept
mutate unpatched reject; git -C "$T/tree/vendor/hardware_overlay" checkout -q -- file.txt
mutate untracked reject; rm -f "$T/tree/vendor/hardware_overlay/newsrc.c"
mutate genmk reject
mutate patched reject
# Regression (baseline run): dev/podman.sh passes Podman 3.4 global options for WSL2.
cat > "$T/bin/podman" <<'X'
#!/bin/bash
echo "$*" >> "$PODMAN_LOG"
case "$*" in *"image inspect"*) echo sha256:feedface ;; esac
exit 0
X
chmod +x "$T/bin/podman"; export PODMAN_LOG="$T/podman.log"; : > "$PODMAN_LOG"
bash "$H/dev/podman.sh" build "$T/tree" --skip-treble-app </dev/null >/dev/null 2>&1
grep -q -- '^--cgroup-manager=cgroupfs --events-backend=file run .*--userns=keep-id' "$PODMAN_LOG" \
    && grep -q -- '^--cgroup-manager=cgroupfs --events-backend=file image exists' "$PODMAN_LOG" \
    && echo "PASS podman.sh passes cgroupfs/file global options" || { echo FAIL; cat "$PODMAN_LOG"; }
: > "$PODMAN_LOG"
DUO_DEV_CGROUP_MANAGER=default DUO_DEV_EVENTS_BACKEND=default bash "$H/dev/podman.sh" build "$T/tree" </dev/null >/dev/null 2>&1
grep -q '^run ' "$PODMAN_LOG" && echo "PASS podman.sh global options can be disabled" || { echo FAIL; cat "$PODMAN_LOG"; }
# Regression (baseline run): interrupted checkouts (all files staged for deletion).
mk_aosp() { # path [local-commit]
    mkdir -p "$T/tree/$1"; git -C "$T/tree/$1" init -q
    seq 1 5 > "$T/tree/$1/Android.bp"; git -C "$T/tree/$1" add -A; git -C "$T/tree/$1" commit -qm base
    git -C "$T/tree/$1" update-ref refs/remotes/m/android-16.0.0_r4 HEAD
    [ -z "${2:-}" ] || git -C "$T/tree/$1" commit -q --allow-empty -m local
    git -C "$T/tree/$1" -c advice.detachedHead=false checkout -q --detach
}
mk_aosp development; mk_aosp developers/build
printf '%s\n' frameworks/base build/make device/phh/treble vendor/hardware_overlay vendor/gapps system/core system/extras development developers/build > "$T/tree/.repo/project.list"
# The mid-build tests above left a stray commit in frameworks/base.
bash "$H/dev/build.sh" --skip-treble-app --reset-patches --label ok1 "$T/tree" >/dev/null 2>&1 && echo "PASS checkout check passes on a clean tree"
git -C "$T/tree/development" rm -q -r .
if bash "$H/dev/build.sh" --skip-treble-app --label broken "$T/tree" 2>"$T/co.err" >/dev/null; then echo "FAIL broken checkout accepted"; else
    grep -q "^  development$" "$T/co.err" && grep -q "reset --hard HEAD" "$T/co.err" && echo "PASS broken checkout refused with repair instructions"; fi
bash "$H/dev/build.sh" --skip-treble-app --repair-checkouts --label repaired "$T/tree" >/dev/null 2>&1 \
    && git -C "$T/tree/development" diff --cached --quiet && [ -f "$T/tree/development/Android.bp" ] && echo "PASS --repair-checkouts restores the checkout"
# A project with a local commit is not reset.
git -C "$T/tree/developers/build" commit -q --allow-empty -m local
git -C "$T/tree/developers/build" rm -q -r .
if bash "$H/dev/build.sh" --skip-treble-app --repair-checkouts "$T/tree" 2>"$T/co.err" >/dev/null; then echo "FAIL repaired project with local commits"; else
    grep -q "not repairing 1 projects" "$T/co.err" && ! [ -f "$T/tree/developers/build/Android.bp" ] && echo "PASS --repair-checkouts leaves projects with local commits alone"; fi
git -C "$T/tree/developers/build" reset -q --hard HEAD
# Patch targets are left to the patch logic, not the checkout repair.
echo staged >> "$T/tree/build/make/file.txt"; git -C "$T/tree/build/make" add file.txt
H0=$(git -C "$T/tree/build/make" rev-parse HEAD)
bash "$H/dev/build.sh" --skip-treble-app --repair-checkouts --patch-only "$T/tree" >/dev/null 2>&1 || true
! git -C "$T/tree/build/make" diff --cached --quiet && [ "$(git -C "$T/tree/build/make" rev-parse HEAD)" == "$H0" ] && echo "PASS --repair-checkouts does not touch patch targets"
git -C "$T/tree/build/make" reset -q --hard HEAD
# M1 gate: --with-probe stages DuoProbe for a dev image only.
mkdir -p "$H/tools/duo-probe/DuoProbe"; echo 'android_app { name: "DuoProbe" }' > "$H/tools/duo-probe/DuoProbe/Android.bp"
git -C "$H" add -A; git -C "$H" commit -qm probe
bash "$H/dev/build.sh" --skip-treble-app --with-probe --label withprobe "$T/tree" >/dev/null 2>"$T/probe.err" || cat "$T/probe.err"
A=$(ls -d "$T/tree/duo-dev-artifacts/"*withprobe 2>/dev/null | head -n1)
[ -f "$T/tree/packages/apps/DuoProbe/Android.bp" ] && [ -f "$T/tree/packages/apps/DuoProbe/.duo-dev-managed" ] \
    && grep -q '^PRODUCT_PACKAGES += DuoProbe' "$T/tree/device/phh/treble/duo-dev.mk" \
    && grep -q '^duo_probe: included' "$A/provenance.txt" && grep -q '^duo_probe: .*apk_sha256 [0-9a-f]\{64\}' "$A/provenance.txt" \
    && echo "PASS --with-probe stages DuoProbe through duo-dev.mk" || echo "FAIL --with-probe"
grep -rq DuoProbe "$H/build" && echo "FAIL a release makefile names DuoProbe" || echo "PASS release makefiles never name DuoProbe"
bash "$H/dev/build.sh" --skip-treble-app --label noprobe "$T/tree" >/dev/null 2>"$T/probe.err" || cat "$T/probe.err"
A=$(ls -d "$T/tree/duo-dev-artifacts/"*noprobe 2>/dev/null | head -n1)
[ ! -e "$T/tree/packages/apps/DuoProbe" ] && ! grep -q DuoProbe "$T/tree/device/phh/treble/duo-dev.mk" \
    && grep -q '^duo_probe: not included' "$A/provenance.txt" \
    && echo "PASS a build without --with-probe removes it" || echo "FAIL probe removal"
mkdir -p "$T/tree/packages/apps/DuoProbe"; echo mine > "$T/tree/packages/apps/DuoProbe/Android.bp"
if bash "$H/dev/build.sh" --skip-treble-app --with-probe --patch-only "$T/tree" >/dev/null 2>&1; then echo "FAIL foreign DuoProbe accepted"; else
    grep -q mine "$T/tree/packages/apps/DuoProbe/Android.bp" && echo "PASS a foreign packages/apps/DuoProbe is left alone"; fi
rm -rf "$T/tree/packages"