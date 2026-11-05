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
# Base-image change on an existing tree: AOSP's obsolete external/chromium-webview
# checkout, in repo v2.71's layout (git dirs in .repo/projects, relative .git
# symlinks, LineageOS projects nested in the old checkout).
echo "---- sync: obsolete AOSP WebView checkout"
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
# repo stub: like v2.71, re-creates missing checkouts of the new projects from
# .repo/projects (_InitWorkTree: relative .git link, read-tree --reset -u), cannot
# remove an obsolete checkout that looks dirty, links the files, updates project.list.
cat > "$T/bin/repo" <<'EOF'
#!/bin/bash
case "$1" in
  sync)
    echo sync >> "${REPO_LOG:-/dev/null}"
    if [ -n "${FAKE_SYNC_ERR:-}" ]; then echo "$FAKE_SYNC_ERR" >&2; exit 1; fi
    W=external/chromium-webview
    if [ -f ".repo/projects/$W/patches.git/HEAD" ] \
        && grep -qs 'remove-project name="platform/external/chromium-webview"' .repo/local_manifests/*.xml; then
      for p in patches prebuilt/arm64; do
        if [ ! -e "$W/$p/.git" ]; then
          mkdir -p "$W/$p"; ln -s "$(realpath -m --relative-to="$W/$p" ".repo/projects/$W/$p.git")" "$W/$p/.git"
          git -C "$W/$p" read-tree --reset -u HEAD
        fi
      done
      if [ -e "$W/.git" ] && [ -n "$(git -C "$W" status --porcelain)" ]; then
        echo "error: $W: Cannot remove project: uncommitted changes are present." >&2; exit 1
      fi
      ln -sfn patches/README "$W/README"; ln -sfn patches/os_pickup.bp "$W/Android.bp"
      printf '%s\n' "$W/patches" "$W/prebuilt/arm64" > .repo/project.list
    fi
    echo "repo sync (stub)" ;;
  manifest) echo '<manifest/>' > "${@: -1}" ;;
  *) echo "repo stub $*" ;;
esac
EOF
chmod +x "$T/bin/repo"
export REPO_LOG="$T/repo.log"
S="$T/synctree"; OW="$S/external/chromium-webview"; GD="$S/.repo/projects/external/chromium-webview.git"
REMOVES_WEBVIEW='<manifest><remove-project name="platform/external/chromium-webview" /></manifest>'
echo "$REMOVES_WEBVIEW" > "$H/build/dev/pinned.xml"
old_files() { mkdir -p "$1/133.0.6943.137/arm64" "$1/prebuilt"; echo apk133 > "$1/133.0.6943.137/arm64/webview.apk"
    echo 'android_app_import { name: "webview" }' > "$1/Android.bp"; echo aosp > "$1/METADATA"; echo old > "$1/prebuilt/METADATA"; }
patches_files() { echo 'android_app_import { name: "webview" }' > "$1/os_pickup.bp"; echo 'android_default_version_name = "156.0.8078.47"' > "$1/README"; }
arm64_files() { echo apk156 > "$1/webview.apk"; }
mk_repo_project() { # tree-path populate-function: repo-style checkout, detached at refs/remotes/m/*
    local wt="$S/$1" gd="$S/.repo/projects/$1.git"
    mkdir -p "$wt" "$(dirname -- "$gd")"
    git init -q --separate-git-dir="$gd" "$wt"
    rm "$wt/.git"; ln -s "$(realpath -m --relative-to="$wt" "$gd")" "$wt/.git"
    "$2" "$wt"; git -C "$wt" add -A; git -C "$wt" commit -qm "$1"
    git -C "$wt" update-ref refs/remotes/m/android-16.0.0_r4 HEAD; git -C "$wt" checkout -q --detach
    git -C "$wt" for-each-ref --format='%(refname:short)' refs/heads/ | xargs -r git -C "$wt" branch -q -D
}
mk_webview_tree() { # [typechange|nonested|linkedwt]: the state a failed sync leaves
    rm -rf "$S" "$T/oldwt" "$T/mainrepo"; mkdir -p "$S/.repo" "$S/external"
    echo external/chromium-webview > "$S/.repo/project.list"
    if [ "${1:-}" == linkedwt ]; then
        git init -q "$T/mainrepo"; old_files "$T/mainrepo"; git -C "$T/mainrepo" add -A; git -C "$T/mainrepo" commit -qm aosp
        git -C "$T/mainrepo" update-ref refs/remotes/m/android-16.0.0_r4 HEAD; git -C "$T/mainrepo" worktree add -q --detach "$OW"
    else
        mk_repo_project external/chromium-webview old_files
    fi
    mk_repo_project external/chromium-webview/patches patches_files
    mk_repo_project external/chromium-webview/prebuilt/arm64 arm64_files
    if [ "${1:-}" == nonested ]; then rm -rf "$OW/patches" "$OW/prebuilt/arm64"; return; fi
    ln -s patches/README "$OW/README"
    if [ "${1:-}" == typechange ]; then rm "$OW/Android.bp"; ln -s patches/os_pickup.bp "$OW/Android.bp"; fi
}
snap() { (cd "$S" && find external .repo/projects -printf '%p|%y|%s|%T@|%l\n' 2>/dev/null | LC_ALL=C sort | sha256sum); }
refused() { # label expected-text [tree-variant] -- setup command...
    local label="$1" want="$2" variant="$3" before; shift 4
    mk_webview_tree "$variant"; [ $# -eq 0 ] || "$@"
    before="$(snap)"; : > "$REPO_LOG"
    if bash "$H/dev/sync.sh" "$S" >/dev/null 2>"$T/sync.err"; then echo "FAIL $label: sync went ahead"; return; fi
    if grep -qF -- "$want" "$T/sync.err" && [ ! -s "$REPO_LOG" ] && [ "$(snap)" == "$before" ] && [ ! -e "$S/.duo-dev/obsolete" ]; then
        echo "PASS obsolete WebView refused, reported, nothing moved: $label"
    else echo "FAIL obsolete WebView: $label (tree $([ "$(snap)" == "$before" ] && echo unchanged || echo CHANGED), repo calls: $(tr '\n' ' ' < "$REPO_LOG"))"; cat "$T/sync.err"; fi
}
g() { git -C "$OW${2:+/$2}" "${@:3}"; }   # g - [nested] git-args...
# Old checkout: every kind of local work, each refused and named.
refused "clean" "No local work found" "" --
refused "unstaged" "' M Android.bp'" "" -- eval 'echo x >> "$OW/Android.bp"'
refused "staged" "'M  METADATA'" "" -- eval 'echo x >> "$OW/METADATA"; g - "" add METADATA'
refused "untracked" "'?? notes.txt'" "" -- eval 'echo x > "$OW/notes.txt"'
refused "ignored" "'!! build.log'" "" -- eval 'echo "*.log" >> "$GD/info/exclude"; echo x > "$OW/build.log"'
refused "committed, not pushed" "(1 commits not in it)" "" -- g - "" commit -q --allow-empty -m local
refused "local branch" "local branches: keep" "" -- g - "" branch -q keep
refused "stash" "stash: 1 entries" "" -- eval 'echo x >> "$OW/METADATA"; g - "" stash -q'
refused "assume-unchanged" "index flags" "" -- g - "" update-index --assume-unchanged METADATA
refused "skip-worktree" "index flags" "" -- g - "" update-index --skip-worktree prebuilt/METADATA
refused "another linked worktree" "another worktree of this repository: $T/oldwt" "" -- g - "" worktree add -q --detach "$T/oldwt"
refused "old checkout is a linked worktree" "is a linked worktree of" linkedwt --
grep -q "^==== one-time migration steps" "$T/sync.err" && echo "FAIL steps printed for a linked worktree" \
    || { grep -q "is not repo's link" "$T/sync.err" && echo "PASS no steps for a checkout that is not repo's link"; }
# Nested LineageOS checkouts (review H1): their local work is named too.
refused "nested staged" "'M  README'" "" -- eval 'echo x >> "$OW/patches/README"; g - patches add README'
refused "nested unstaged" "' M webview.apk'" "" -- eval 'echo x >> "$OW/prebuilt/arm64/webview.apk"'
refused "nested committed" "(1 commits not in it)" "" -- g - patches commit -q --allow-empty -m local
refused "nested stash" "stash: 1 entries" "" -- eval 'echo x >> "$OW/patches/README"; g - patches stash -q'
refused "nested ignored" "'!! x.log'" "" -- eval 'echo "*.log" >> "$S/.repo/projects/external/chromium-webview/prebuilt/arm64.git/info/exclude"; echo x > "$OW/prebuilt/arm64/x.log"'
refused "nested skip-worktree" "index flags" "" -- g - patches update-index --skip-worktree README
grep -q "Local work in the LineageOS checkout external/chromium-webview/patches" "$T/sync.err" \
    && echo "PASS nested local work is attributed to its checkout" || echo "FAIL nested attribution"
mk_webview_tree; echo '<manifest/>' > "$H/build/dev/pinned.xml"; : > "$REPO_LOG"
bash "$H/dev/sync.sh" "$S" >/dev/null 2>&1 || true; [ -L "$OW/.git" ] && [ -s "$REPO_LOG" ] && [ ! -e "$S/.duo-dev/obsolete" ] \
    && echo "PASS the WebView checkout is left to repo when the manifest does not remove it" || echo "FAIL WebView kept"
echo "$REMOVES_WEBVIEW" > "$H/build/dev/pinned.xml"
MIG="$S/.duo-dev/webview-migration.sh"; MARKER="$S/.duo-dev/webview-migration.incomplete"
run_steps() { bash "$MIG"; }
# dev/sync.sh writes the migration script, prints it in full and runs none of it.
mk_webview_tree; bash "$H/dev/sync.sh" "$S" >/dev/null 2>"$T/sync.err" || true
sed -n '/^==== .duo-dev\/webview-migration.sh ====$/,/^==== end of .duo-dev\/webview-migration.sh ====$/{/^====/d;p}' "$T/sync.err" | cmp -s - "$MIG" \
    && grep -q "^    bash .*/.duo-dev/webview-migration.sh$" "$T/sync.err" && [ ! -e "$MARKER" ] && [ -L "$OW/.git" ] \
    && echo "PASS the migration is written and printed as one script, not run" || { echo "FAIL migration script"; cat "$T/sync.err"; }
# The script holds the tree lock: nothing happens while a sync or build holds it.
before="$(snap)"; ( flock 8; sleep 5 ) 8>"$S/.duo-dev/lock" & sleep 1
if bash "$MIG" >/dev/null 2>"$T/mig.err"; then echo "FAIL migration ran under the tree lock"
elif grep -q "is running on this tree" "$T/mig.err" && [ ! -e "$MARKER" ] && [ ! -e "$S/.duo-dev/webview-nested" ] && [ "$(snap)" == "$before" ]; then
    echo "PASS the migration refuses while the tree lock is held"
else echo "FAIL migration under lock"; cat "$T/mig.err"; fi
wait
# Interrupted before each of its commands (kill -9 from a DEBUG trap): a sync
# attempt is refused without touching the parked checkouts' index, and running
# the script again completes the migration.
cat > "$T/interrupt.env" <<'EOF'
__duo_n=0
trap '__duo_n=$((__duo_n + 1)); [ "$__duo_n" -lt "$DUO_TEST_STOP" ] || kill -9 $$' DEBUG
EOF
idx() { sha256sum "$S/.repo/projects/external/chromium-webview/$1.git/index" | cut -d' ' -f1; }
mk_staged_tree() {
    mk_webview_tree typechange
    echo staged >> "$OW/prebuilt/METADATA"; g - "" add prebuilt/METADATA
    echo staged >> "$OW/patches/README"; g - patches add README
    echo staged >> "$OW/prebuilt/arm64/webview.apk"; g - prebuilt/arm64 add webview.apk
}
# The tree is re-created at the same path, so one generated script serves every run.
mk_staged_tree; bash "$H/dev/sync.sh" "$S" >/dev/null 2>&1 || true; cp "$MIG" "$T/migration.sh"
points=0; bad=0; done_early=0
for ((n = 1; n < 200; n++)); do
    mk_staged_tree; mkdir -p "$S/.duo-dev"; cp "$T/migration.sh" "$MIG"
    ip=$(idx patches); ia=$(idx prebuilt/arm64)
    if BASH_ENV="$T/interrupt.env" DUO_TEST_STOP=$n bash "$MIG" >/dev/null 2>&1; then break; fi
    points=$((points + 1)); : > "$REPO_LOG"
    if [ ! -e "$MARKER" ] && [ ! -e "$S/.duo-dev/webview-nested" ] && [ ! -e "$OW/.git" ] && [ -d "$OW/patches" ]; then
        done_early=$((done_early + 1))   # stopped after its last effect: the migration is complete, sync may run
    elif bash "$H/dev/sync.sh" "$S" >/dev/null 2>"$T/int.err" || [ -s "$REPO_LOG" ] \
        || [ "$(idx patches)" != "$ip" ] || [ "$(idx prebuilt/arm64)" != "$ia" ]; then
        echo "FAIL interrupted before command $n: sync not refused, or a parked checkout's index changed"; cat "$T/int.err"; bad=1; break
    fi
    D=""
    if ! { { [ "$done_early" -gt 0 ] || bash "$MIG" >/dev/null 2>"$T/int.err"; } && bash "$H/dev/sync.sh" "$S" >/dev/null 2>>"$T/int.err"; } \
        || [ "$(git -C "$OW/patches" diff --cached --name-only)" != README ] \
        || [ "$(git -C "$OW/prebuilt/arm64" diff --cached --name-only)" != webview.apk ] \
        || ! D=$(ls -d "$S/.duo-dev/obsolete/external-chromium-webview."* 2>/dev/null) \
        || [ "$(git -C "$D" status --porcelain=v1 2>&1)" != "M  prebuilt/METADATA" ] \
        || [ -L "$D/Android.bp" ] || [ "$(cat "$D/Android.bp" 2>&1)" != 'android_app_import { name: "webview" }' ] \
        || [ -e "$D/README" ] || [ -L "$D/README" ] || compgen -G "$MARKER.restore-*" >/dev/null \
        || [ -e "$MARKER" ] || [ -e "$S/.duo-dev/webview-nested" ]; then
        echo "FAIL resuming after an interruption before command $n"; cat "$T/int.err"; [ -z "$D" ] || { git -C "$D" status --porcelain=v1; ls -la "$D"; }; bad=1; break
    fi
done
[ "$bad" -eq 0 ] && [ "$points" -ge 15 ] && [ "$done_early" -le 1 ] \
    && echo "PASS interrupted before each of its $points commands: sync refused while unfinished ($done_early stop after completion), staged indexes kept, rerun completes with the archived work tree intact" \
    || { [ "$bad" -ne 0 ] || echo "FAIL only $points interruption points"; }
# A parked checkout without the marker (e.g. moved by hand) also stops sync.
mk_webview_tree; mkdir -p "$S/.duo-dev/webview-nested"; mv "$OW/patches" "$S/.duo-dev/webview-nested/patches"
before="$(snap)"; : > "$REPO_LOG"
if bash "$H/dev/sync.sh" "$S" >/dev/null 2>"$T/sync.err"; then echo "FAIL sync with a parked checkout"
elif grep -q "parked: .duo-dev/webview-nested/patches" "$T/sync.err" && [ ! -s "$REPO_LOG" ] && [ "$(snap)" == "$before" ]; then
    echo "PASS a parked checkout without the marker also stops sync"
else echo "FAIL parked checkout"; cat "$T/sync.err"; fi
# Only repo's two linkfiles (exact targets) are removed from the old checkout;
# a user's symlink into patches/ is local work: reported and kept.
mk_webview_tree; rm "$OW/README"; ln -s patches/my-notes "$OW/README"; ln -s patches/x "$OW/notes"
bash "$H/dev/sync.sh" "$S" >/dev/null 2>"$T/sync.err" && echo "FAIL sync went ahead"
if grep -qF "'?? README'" "$T/sync.err" && grep -qF "'?? notes'" "$T/sync.err" && run_steps >/dev/null 2>&1 && bash "$H/dev/sync.sh" "$S" >/dev/null 2>&1; then
    D=$(ls -d "$S/.duo-dev/obsolete/external-chromium-webview."* 2>/dev/null || true)
    [ "$(readlink "$D/README" 2>&1)" == patches/my-notes ] && [ "$(readlink "$D/notes" 2>&1)" == patches/x ] \
        && echo "PASS user symlinks into patches/ are reported and kept, not removed" || { echo "FAIL user symlinks"; ls -la "$D"; }
else echo "FAIL user symlinks"; cat "$T/sync.err"; fi
# The migration, then a (mocked) repo sync: everything is kept and usable.
mk_webview_tree typechange
g - "" commit -q --allow-empty -m local; OLDHEAD=$(g - "" rev-parse HEAD)
echo stashed >> "$OW/METADATA"; g - "" stash -q; g - "" branch -q keep
echo staged >> "$OW/prebuilt/METADATA"; g - "" add prebuilt/METADATA; echo unstaged >> "$OW/METADATA"
echo "*.log" >> "$GD/info/exclude"; echo log > "$OW/build.log"; g - "" update-index --assume-unchanged 133.0.6943.137/arm64/webview.apk
g - "" worktree add -q --detach "$T/oldwt"
echo staged >> "$OW/patches/README"; g - patches add README; echo unstaged >> "$OW/prebuilt/arm64/webview.apk"
bash "$H/dev/sync.sh" "$S" >/dev/null 2>"$T/sync.err" && echo "FAIL sync went ahead"
if run_steps >"$T/steps.out" 2>&1 && bash "$H/dev/sync.sh" "$S" >/dev/null 2>"$T/sync2.err"; then
    D=$(ls -d "$S/.duo-dev/obsolete/external-chromium-webview."* 2>/dev/null || true)
    st=$(git -C "$D" status --porcelain=v1 --ignored 2>&1 || true)
    [ "$(git -C "$D" rev-parse HEAD 2>&1 || true)" == "$OLDHEAD" ] && grep -qx 'M  prebuilt/METADATA' <<<"$st" && grep -qx ' M METADATA' <<<"$st" \
        && grep -qx '!! build.log' <<<"$st" && [ "$(grep -vc -e '^M  prebuilt/METADATA$' -e '^ M METADATA$' -e '^!! build.log$' <<<"$st")" -eq 0 ] \
        && [ ! -L "$D/Android.bp" ] && grep -q webview "$D/Android.bp" && [ "$(git -C "$D" stash list | wc -l)" -eq 1 ] \
        && git -C "$D" rev-parse -q --verify refs/heads/keep >/dev/null && git -C "$D" ls-files -v | grep -q '^h 133' \
        && git -C "$T/oldwt" status --porcelain >/dev/null && [ -f "$S/.duo-dev/obsolete/.find-ignore" ] \
        && echo "PASS the moved old checkout keeps HEAD, index, stash, branch, ignored files, index flags and worktrees" \
        || { echo "FAIL moved old checkout"; git -C "$D" status --porcelain=v1 --ignored || true; }
    [ "$(git -C "$OW/patches" diff --cached --name-only)" == README ] && [ "$(git -C "$OW/prebuilt/arm64" diff --name-only)" == webview.apk ] \
        && [ ! -e "$OW/.git" ] && [ "$(readlink "$OW/Android.bp")" == patches/os_pickup.bp ] && ! grep -qx external/chromium-webview "$S/.repo/project.list" \
        && echo "PASS the LineageOS checkouts keep their staged and unstaged changes through the migration and sync" \
        || { echo "FAIL nested checkouts after migration"; git -C "$OW/patches" status; }
    bash "$H/dev/sync.sh" "$S" >/dev/null 2>&1 && echo "PASS sync runs again after the migration"
else echo "FAIL migration steps or the following sync"; cat "$T/steps.out" "$T/sync2.err" 2>/dev/null; fi
mk_webview_tree nonested; echo edit >> "$OW/METADATA"
bash "$H/dev/sync.sh" "$S" >/dev/null 2>"$T/sync.err" && echo "FAIL sync went ahead"
if run_steps >/dev/null 2>&1 && bash "$H/dev/sync.sh" "$S" >/dev/null 2>&1; then
    D=$(ls -d "$S/.duo-dev/obsolete/external-chromium-webview."* 2>/dev/null || true)
    [ "$(git -C "$D" status --porcelain 2>&1 || true)" == " M METADATA" ] && [ -f "$OW/patches/os_pickup.bp" ] && [ -f "$OW/prebuilt/arm64/webview.apk" ] \
        && echo "PASS the migration also works before the LineageOS checkouts exist" || echo "FAIL steps without nested checkouts"
else echo "FAIL steps without nested checkouts"; cat "$T/sync.err"; fi
rm -rf "$S"; mkdir -p "$S"; t0=$SECONDS
if FAKE_SYNC_ERR="error: external/foo: Cannot remove project: uncommitted changes are present." bash "$H/dev/sync.sh" --attempts 3 "$S" >/dev/null 2>"$T/sync.err"; then echo "FAIL sync error ignored"
elif grep -q "retrying would not help" "$T/sync.err" && ! grep -q "retrying in" "$T/sync.err" && [ $((SECONDS - t0)) -lt 30 ]; then echo "PASS 'Cannot remove project' stops without retries"
else echo "FAIL Cannot remove project"; cat "$T/sync.err"; fi
FAKE_SYNC_ERR="fatal: unable to access 'https://example.invalid/': Could not resolve host" bash "$H/dev/sync.sh" --attempts 1 "$S" >/dev/null 2>"$T/sync.err" || true
grep -q "not an HTTP 429" "$T/sync.err" && echo "PASS a non-429 sync failure is not reported as rate limiting" || { echo "FAIL non-429 message"; cat "$T/sync.err"; }
FAKE_SYNC_ERR="error: RPC failed; HTTP 429 curl 22 The requested URL returned error: 429" bash "$H/dev/sync.sh" --attempts 1 "$S" >/dev/null 2>"$T/sync.err" || true
grep -q "HTTP 429 rate limiting" "$T/sync.err" && echo "PASS an HTTP 429 sync failure is reported as rate limiting" || { echo "FAIL 429 message"; cat "$T/sync.err"; }
