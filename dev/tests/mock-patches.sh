#!/bin/bash
# Mock test of dev/build.sh patch handling (--patch-only), on synthetic git repos.
# Usage: dev/tests/mock-patches.sh <harness-root>; run via dev/tests/run.sh.
# shellcheck disable=SC2015,SC2012
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
# Regression (baseline run): shallow patched projects fail early; --unshallow fetches history.
S="$T/src/core"; mkdir -p "$S"; git -C "$S" init -q -b main
echo one > "$S/file.txt"; git -C "$S" add -A; git -C "$S" commit -qm one
echo two >> "$S/file.txt"; git -C "$S" commit -qam two
git -C "$S" checkout -q -b tmp; echo core1 >> "$S/file.txt"; git -C "$S" commit -qam core1
mkdir -p "$H/patches/duo/platform_system_core"
git -C "$S" format-patch -q -1 --stdout > "$H/patches/duo/platform_system_core/0001-core1.patch"
git -C "$S" checkout -q main
git clone -q --depth=1 --branch main "file://$S" "$T/tree/system/core"
git -C "$T/tree/system/core" update-ref refs/remotes/m/android-16.0.0_r4 HEAD
git -C "$T/tree/system/core" -c advice.detachedHead=false checkout -q --detach
git -C "$H" add -A; git -C "$H" commit -qm "core patch"
if run 2>"$T/shallow.err"; then echo "FAIL shallow patched project accepted"; else
    grep -q "system/core" "$T/shallow.err" && grep -q -- "--unshallow" "$T/shallow.err" && echo "PASS shallow patched project refused with instructions"; fi
[ "$(git -C "$T/tree/system/core" rev-parse --is-shallow-repository)" == true ] && echo "PASS shallow project untouched without --unshallow"
run --unshallow >/dev/null 2>&1 && echo "PASS --unshallow applies patches"
[ "$(git -C "$T/tree/system/core" rev-parse --is-shallow-repository)" == false ] && echo "PASS project unshallowed"
git -C "$T/tree/system/core" log --format=%s | tr '\n' ' ' | grep -q "core1 two one" && echo "PASS full history and patch present"
# Regression (baseline run): a patch base reachable only from an earlier release tag.
S="$T/src/extras"; mkdir -p "$S"; git -C "$S" init -q -b main
echo root > "$S/file.txt"; git -C "$S" add -A; git -C "$S" commit -qm root
git -C "$S" checkout -q -b r2
seq 1 9 > "$S/other.txt"; git -C "$S" add -A; git -C "$S" commit -qm "r2 other"; git -C "$S" tag android-16.0.0_r2
sed -i 's/^5$/FIVE/' "$S/other.txt"; git -C "$S" commit -qam extras1
mkdir -p "$H/patches/duo/platform_system_extras"
git -C "$S" format-patch -q -1 --stdout > "$H/patches/duo/platform_system_extras/0001-extras1.patch"
git -C "$S" checkout -q main; seq 1 9 | sed 's/^8$/EIGHT/' > "$S/other.txt"; git -C "$S" add -A; git -C "$S" commit -qm "r4 other"
git clone -q --no-tags --single-branch --branch main -o aosp "file://$S" "$T/tree/system/extras"
git -C "$T/tree/system/extras" update-ref refs/remotes/m/android-16.0.0_r4 HEAD
git -C "$T/tree/system/extras" -c advice.detachedHead=false checkout -q --detach
git -C "$H" add -A; git -C "$H" commit -qm "extras patch"
if run >"$T/tags.out" 2>&1; then echo "FAIL patch needing a base tag applied without it"; else
    grep -q "lack the android-16.0.0_r\* base tags" "$T/tags.out" && echo "PASS missing base tags warned, apply fails"; fi
run --reset-patches --unshallow >/dev/null 2>&1 && echo "PASS --unshallow fetches base tags and applies"
git -C "$T/tree/system/extras" rev-parse -q --verify refs/tags/android-16.0.0_r2 >/dev/null && echo "PASS android-16.0.0_r2 tag fetched"
grep -qx system/extras "$T/tree/.duo-dev/base-tags-fetched" && ! grep -q system/core "$T/tree/.duo-dev/base-tags-fetched" \
    && echo "PASS tags fetched only for aosp-remote projects"

# dev/check-apply.sh: full series on a clean tree, all failures reported.
CA="$H/dev/check-apply.sh"
bash "$CA" --restore "$T/tree" >"$T/ca.out" 2>&1 && grep -q "All patches apply" "$T/ca.out" && echo "PASS check-apply clean series"
[ "$(git -C "$T/tree/frameworks/base" rev-parse HEAD)" == "$(git -C "$T/tree/frameworks/base" rev-parse refs/remotes/m/android-16.0.0_r4)" ] && echo "PASS check-apply --restore returns to base"
for f in "$H/patches/trebledroid/platform_frameworks_base/"*.patch "$H/patches/personal/platform_build/"*.patch; do
    sed -i 's/^ base$/ nope/; s/^index .*/index 0000000..1111111 100644/' "$f"
done
if bash "$CA" "$T/tree" >"$T/ca.out" 2>&1; then echo "FAIL check-apply accepted broken series"; else
    grep -q "failed: 2, skipped after a failure: 1" "$T/ca.out" && grep -q "trebledroid/platform_frameworks_base" "$T/ca.out" \
        && grep -q "personal/platform_build" "$T/ca.out" && echo "PASS check-apply reports every failing project" || cat "$T/ca.out"
fi
git -C "$T/tree/device/phh/treble" log --format=%s | grep -q duo2 && echo "PASS check-apply continues with other projects"
git -C "$H" checkout -q -- patches
bash "$CA" --restore "$T/tree" >/dev/null 2>&1; run >/dev/null 2>&1

# Project-name mapping: dev/lib.sh and patch.sh agree, including tree paths that contain '_'.
(
    # shellcheck source=/dev/null
    source "$H/dev/lib.sh"
    ok=true
    for pair in platform_frameworks_base:frameworks/base platform_build:build/make treble_app:treble_app \
        vendor_hardware_overlay:vendor/hardware_overlay device_phh_treble:device/phh/treble \
        packages_thain_services_PostureProcessor:packages/thain/services/PostureProcessor \
        packages_thain_rro_overlays:packages/thain/rro_overlays; do
        got="$(duo_project_path "${pair%%:*}")"
        [ "$got" == "${pair#*:}" ] || { echo "FAIL duo_project_path ${pair%%:*} -> $got"; ok=false; }
    done
    if $ok; then echo "PASS duo_project_path maps every known project"; fi
)
# End to end through build.sh (lib.sh mapping) and patch.sh (its own copy of the mapping).
mk_project packages/thain/rro_overlays; mk_project packages/thain/services/PostureProcessor
mk_patch duo packages_thain_rro_overlays packages/thain/rro_overlays rro1
mk_patch duo packages_thain_services_PostureProcessor packages/thain/services/PostureProcessor pp1
for p in packages/thain/rro_overlays packages/thain/services/PostureProcessor; do
    git -C "$T/tree/$p" checkout -q --detach refs/remotes/m/android-16.0.0_r4
done
git -C "$H" add -A; git -C "$H" commit -qm "thain patches"
if run >"$T/thain.out" 2>&1; then
    git -C "$T/tree/packages/thain/rro_overlays" log --format=%s | grep -qx rro1 && echo "PASS packages_thain_rro_overlays patched in packages/thain/rro_overlays"
    git -C "$T/tree/packages/thain/services/PostureProcessor" log --format=%s | grep -qx pp1 && echo "PASS packages_thain_services_PostureProcessor patched"
else
    echo "FAIL thain patch projects"; cat "$T/thain.out"
fi