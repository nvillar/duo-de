#!/bin/bash
# Stubbed full run of dev/build.sh (fake lunch/make/repo) plus sync/build locking.
# Runs after mock-patches.sh in the same DUO_TEST_DIR; run via dev/tests/run.sh.
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
    head -c 1048576 /dev/urandom > "$OUT/system.img";;
  esac
}
EOF
mkdir -p "$T/tree/vendor/hardware_overlay/TrebleApp"; echo apk > "$T/tree/vendor/hardware_overlay/TrebleApp/app.apk"
mkdir -p "$T/tree/.repo/local_manifests"; cp "$H/build/dev/pinned.xml" "$T/tree/.repo/local_manifests/duo-de-pinned.xml"
bash "$H/dev/build.sh" --skip-treble-app --label mock --jobs 2 "$T/tree"
A=$(ls -d "$T/tree/duo-dev-artifacts/"*mock); ls -la "$A"; cat "$A/provenance.txt"; (cd "$A" && sha256sum -c SHA256SUMS)
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
# Regression (review #1): shared lock.
mkdir -p "$T/tree/.duo-dev"; cp "$T/tree/.repo/local_manifests/duo-de-pinned.xml" "$T/lm.before"
( flock 8; sleep 8 ) 8>"$T/tree/.duo-dev/lock" & sleep 1
if bash "$H/dev/sync.sh" --no-sync "$T/tree" 2>/dev/null; then echo "FAIL sync ran under lock"; else echo "PASS sync refuses while locked"; fi
cmp -s "$T/lm.before" "$T/tree/.repo/local_manifests/duo-de-pinned.xml" && echo "PASS manifests untouched under lock"
if bash "$H/dev/build.sh" --skip-treble-app "$T/tree" 2>/dev/null; then echo "FAIL build ran under lock"; else echo "PASS build refuses while locked"; fi
wait
bash "$H/dev/sync.sh" --no-sync "$T/tree" 2>/dev/null && echo "PASS sync --no-sync runs when unlocked"
# Regression (review #1): tree changed mid-build -> no artifacts.
cat >> "$T/tree/build/envsetup.sh" <<'X'
if [ -n "${MUTATE:-}" ]; then eval "orig_$(declare -f make)"; make() { orig_make "$@"; case "$*" in *systemimage*) git -C frameworks/base -c user.name=x -c user.email=x@x commit -q --allow-empty -m concurrent;; esac; }; fi
X
if MUTATE=1 bash "$H/dev/build.sh" --skip-treble-app --label mut "$T/tree" 2>"$T/mut.err"; then echo "FAIL mutated tree accepted"; else echo "PASS mutated tree rejected"; fi
grep -m1 "changed during the build" "$T/mut.err"
ls -d "$T/tree/duo-dev-artifacts/"*mut 2>/dev/null && echo "FAIL artifacts written" || echo "PASS no artifacts for mutated build"