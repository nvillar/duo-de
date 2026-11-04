#!/bin/bash
# Mock test of dev/build.sh patch handling (--patch-only), on synthetic git repos.
# Usage: dev/tests/mock-patches.sh <harness-root>; run via dev/tests/run.sh.
set -euo pipefail
SRC="$1"
T="${DUO_TEST_DIR:?}"
rm -rf "$T"; mkdir -p "$T"
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
mkdir -p "$T/bin"; printf '#!/bin/sh\necho "repo stub $*"\n' > "$T/bin/repo"; chmod +x "$T/bin/repo"
export PATH="$T/bin:$PATH"

# Harness copy with synthetic patches.
H="$T/harness"; mkdir -p "$H"
cp -r "$SRC/dev" "$SRC/build" "$SRC/patch.sh" "$H/"
find "$H" -type f -exec sed -i 's/\r$//' {} +
mk_project() { # path
    mkdir -p "$T/tree/$1"; git -C "$T/tree/$1" init -q
    echo base > "$T/tree/$1/file.txt"; git -C "$T/tree/$1" add -A; git -C "$T/tree/$1" commit -qm base
    git -C "$T/tree/$1" update-ref refs/remotes/m/android-16.0.0_r4 HEAD
    git -C "$T/tree/$1" -c advice.detachedHead=false checkout -q --detach
}
mk_patch() { # set project path line
    local w="$T/w"; rm -rf "$w"; git clone -q "$T/tree/$3" "$w"; git -C "$w" checkout -q "$(git -C "$T/tree/$3" rev-parse HEAD)"
    echo "$4" >> "$w/file.txt"; git -C "$w" commit -qam "$4"
    mkdir -p "$H/patches/$1/$2"
    n=$(ls "$H/patches/$1/$2" | wc -l); git -C "$w" format-patch -q -1 --stdout > "$H/patches/$1/$2/000$((n+1))-$4.patch"
    git -C "$T/tree/$3" am -q "$H/patches/$1/$2/000$((n+1))-$4.patch"   # advance so next patch stacks
}
rm -rf "$H/patches"; mkdir -p "$T/tree/.repo/manifests" "$T/tree/build"; touch "$T/tree/build/envsetup.sh"
mk_project frameworks/base; mk_project build/make; mk_project device/phh/treble; mk_project vendor/hardware_overlay
cat > "$T/tree/device/phh/treble/generate.sh" <<'EOF'
#!/bin/bash
echo "PRODUCT_MAKEFILES := \\" > AndroidProducts.mk
printf '$(call inherit-product, %s)\n' "$1" > treble_arm64_bvN.mk
EOF
mk_patch trebledroid platform_frameworks_base frameworks/base td1
mk_patch personal platform_build build/make p1
mk_patch duo platform_frameworks_base frameworks/base duo1
mk_patch duo device_phh_treble device/phh/treble duo2
mkdir -p "$H/patches/duo/vendor_gapps"; cp "$H/patches/duo/device_phh_treble/"*.patch "$H/patches/duo/vendor_gapps/"
# Reset tree projects to base.
for p in frameworks/base build/make device/phh/treble; do git -C "$T/tree/$p" checkout -q --detach refs/remotes/m/android-16.0.0_r4; done
git -C "$H" init -q; git -C "$H" add -A; git -C "$H" commit -qm harness

run() { echo "---- $*"; bash "$H/dev/build.sh" --patch-only "$@" "$T/tree"; }
head_of() { git -C "$T/tree/$1" log --format=%s | tr '\n' ' '; echo; }

run; head_of frameworks/base; cat "$T/tree/.duo-dev/patch-state"
H1=$(git -C "$T/tree/frameworks/base" rev-parse HEAD)
run; [ "$(git -C "$T/tree/frameworks/base" rev-parse HEAD)" == "$H1" ] && echo "PASS idempotent"
echo "local edit" >> "$T/tree/frameworks/base/file.txt"; run && echo "PASS keeps local edits"; git -C "$T/tree/frameworks/base" checkout -q -- file.txt
# Change patch set -> re-apply from base, deterministic SHA?
echo "# touched" >> "$H/patch.sh"; run; [ "$(git -C "$T/tree/frameworks/base" rev-parse HEAD)" == "$H1" ] && echo "PASS deterministic reapply"
# Foreign commit -> refuse
git -C "$T/tree/build/make" commit -q --allow-empty -m foreign
if run; then echo "FAIL foreign accepted"; else echo "PASS refused foreign commit"; fi
run --reset-patches && echo "PASS reset"; head_of build/make
# Broken patch -> loud failure, then reset recovers
sed -i 's/^+duo1$/+duoX/; s/^ td1$/ nope/; s/^index .*/index 0000000..1111111 100644/' "$H/patches/duo/platform_frameworks_base/"*duo1.patch
if run; then echo "FAIL broken accepted"; else echo "PASS broken patch fails"; fi
git -C "$H" checkout -q -- patches; git -C "$H" checkout -q -- patch.sh
if run; then echo "FAIL stuck am accepted"; else echo "PASS stuck am refused"; fi
run --reset-patches && echo "PASS recovered"; head_of frameworks/base
# Regression (review #2): --reset-patches must not take the fast path and must discard tracked edits.
echo "tracked edit" >> "$T/tree/frameworks/base/file.txt"
run --reset-patches
if git -C "$T/tree/frameworks/base" diff --quiet HEAD; then echo "PASS reset discards tracked edits"; else echo "FAIL reset kept tracked edits"; fi
grep -c . "$T/tree/device/phh/treble/duo-dev.mk"; cat "$T/tree/device/phh/treble/duo-dev.mk"
# gapps present -> its patches applied
mk_project vendor/gapps; git -C "$T/tree/vendor/gapps" checkout -q -- . ; run; head_of vendor/gapps
