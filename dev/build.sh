#!/bin/bash
# DUO-DE developer build: vanilla treble_arm64_bvN-bp2a-userdebug system image,
# signed with the AOSP test keys, with the in-ROM updater pointed away from the
# upstream OTA feed.
#
# Usage: dev/build.sh [options] <aosp-tree>
#
#   --artifacts-dir DIR  where to put results (default: <aosp-tree>/duo-dev-artifacts)
#   --jobs N             parallel build jobs (default: nproc)
#   --label TEXT         suffix for the build id ([A-Za-z0-9_-], up to 24 chars)
#   --ota-url URL        updater feed baked into the image (https only; default
#                        https://ota-disabled.invalid/duo-de-dev/ota.json)
#   --reset-patches      abort stuck `git am` runs and force patched projects back
#                        to their manifest revision, discarding tracked changes
#   --skip-patches       use the tree's current state, do not touch patches
#   --skip-treble-app    keep vendor/hardware_overlay's TrebleApp; do not rebuild it
#   --no-installclean    skip `make installclean` (faster, may keep stale files)
#   --patch-only         apply patches and generate makefiles, then stop
#   --unshallow          fetch full history for shallow projects that receive
#                        patches (git am -3 needs the patches' base blobs);
#                        and android-16.0.0_r* tags; without it a shallow
#                        patched project is an error
#   --repair-checkouts   `git reset --hard HEAD` unpatched projects whose index
#                        differs from HEAD (interrupted checkout) and that have
#                        no local commits; without it such projects are an error
#   --with-probe         dev only: copy tools/duo-probe/DuoProbe to
#                        <aosp-tree>/packages/apps/DuoProbe and add it to the
#                        untracked duo-dev.mk; a build without it removes both
#
# Writes only inside <aosp-tree> and the artifacts directory. Never uploads,
# never uses release keys, never runs `git clean` or `repo sync`.
# Re-runnable: patches are applied once and recorded in <aosp-tree>/.duo-dev/.

set -euo pipefail
# shellcheck source=dev/lib.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"

ARTIFACTS_DIR=""
JOBS="$(duo_jobs_default)"
LABEL=""
OTA_URL="$DUO_DEFAULT_OTA_URL"
RESET_PATCHES=false
SKIP_PATCHES=false
SKIP_TREBLE_APP=false
INSTALLCLEAN=true
PATCH_ONLY=false
UNSHALLOW=false
REPAIR_CHECKOUTS=false
WITH_PROBE=false
TREE_ARG=""

usage() { sed -n '2,33p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --artifacts-dir) ARTIFACTS_DIR="${2:?--artifacts-dir needs a value}"; shift 2 ;;
        --jobs) JOBS="${2:?--jobs needs a value}"; shift 2 ;;
        --label) LABEL="${2:?--label needs a value}"; shift 2 ;;
        --ota-url) OTA_URL="${2:?--ota-url needs a value}"; shift 2 ;;
        --reset-patches) RESET_PATCHES=true; shift ;;
        --skip-patches) SKIP_PATCHES=true; shift ;;
        --skip-treble-app) SKIP_TREBLE_APP=true; shift ;;
        --no-installclean) INSTALLCLEAN=false; shift ;;
        --patch-only) PATCH_ONLY=true; shift ;;
        --unshallow) UNSHALLOW=true; shift ;;
        --repair-checkouts) REPAIR_CHECKOUTS=true; shift ;;
        --with-probe) WITH_PROBE=true; shift ;;
        -h|--help) usage; exit 0 ;;
        -*) die "unknown option $1 (see --help)" ;;
        *) [ -z "$TREE_ARG" ] || die "only one <aosp-tree> allowed"; TREE_ARG="$1"; shift ;;
    esac
done
[ -n "$TREE_ARG" ] || { usage; exit 2; }
[[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || die "--jobs must be a positive number"
[ -z "$LABEL" ] || [[ "$LABEL" =~ ^[A-Za-z0-9_-]{1,24}$ ]] || die "--label must match [A-Za-z0-9_-]{1,24}"
[[ "$OTA_URL" =~ ^https://[A-Za-z0-9._~:/?=%+-]+$ ]] || die "--ota-url must be a plain https:// URL"
shopt -s nocasematch
[[ "$OTA_URL" != *archfx* ]] || die "--ota-url must not point at the upstream (Archfx) feed"
shopt -u nocasematch
if $SKIP_PATCHES && $RESET_PATCHES; then die "--skip-patches and --reset-patches conflict"; fi

duo_require_cmds git repo python3 sha256sum cmp flock sed
HARNESS="$(duo_harness_root)"
TREE="$(duo_tree_path "$TREE_ARG")"
[ -f "$TREE/build/envsetup.sh" ] && [ -d "$TREE/.repo" ] \
    || die "$TREE is not a synced Android tree (see dev/sync.sh)"
[ -n "$ARTIFACTS_DIR" ] || ARTIFACTS_DIR="$TREE/duo-dev-artifacts"

STATE_DIR="$TREE/.duo-dev"
STATE_FILE="$STATE_DIR/patch-state"
mkdir -p "$STATE_DIR"
exec 9>"$STATE_DIR/lock"
flock -n 9 || die "a dev/build.sh or dev/sync.sh is running on $TREE"

cd -- "$TREE"

# ---------------------------------------------------------------- identity

HARNESS_COMMIT="$(git -C "$HARNESS" rev-parse HEAD)"
HARNESS_COMMIT_DATE="$(git -C "$HARNESS" log -1 --format=%cI HEAD)"
if [ -n "$(git -C "$HARNESS" --no-optional-locks status --porcelain)" ]; then
    HARNESS_DIRTY=true
else
    HARNESS_DIRTY=false
fi
BUILD_TAG="duodev.$(date -u +%Y%m%d%H%M%S).${HARNESS_COMMIT:0:8}"
$HARNESS_DIRTY && BUILD_TAG+=".dirty"
[ -z "$LABEL" ] || BUILD_TAG+=".$LABEL"
log "Build id: $BUILD_TAG"
$HARNESS_DIRTY && warn "harness checkout $HARNESS has uncommitted changes"

# ---------------------------------------------------------------- patches

GAPPS_PRESENT=false
[ -d vendor/gapps ] && GAPPS_PRESENT=true

PATCH_ROOT="$STATE_DIR/patchroot"
declare -a PROJECT_PATHS=()   # unique tree paths touched by the included patches
declare -A SEEN=()

patchset_hash() {
    local set
    {
        sha256sum "$HARNESS/patch.sh" | cut -d' ' -f1
        for set in "${DUO_PATCH_SETS[@]}"; do
            (cd -- "$PATCH_ROOT/patches/$set" && LC_ALL=C find -L . -name '*.patch' -type f | LC_ALL=C sort \
                | while read -r f; do printf '%s %s\n' "$set/${f#./}" "$(sha256sum < "$f" | cut -d' ' -f1)"; done)
        done
    } | sha256sum | cut -d' ' -f1
}

state_get() { [ -f "$STATE_FILE" ] && awk -v k="$1" '$1 == k { print $2; exit }' "$STATE_FILE"; }
state_head() { [ -f "$STATE_FILE" ] && awk -v p="$1" '$1 == "project" && $2 == p { print $4; exit }' "$STATE_FILE"; }
# Manifest revision the project's patches were applied on top of.
state_base() { [ -f "$STATE_FILE" ] && awk -v p="$1" '$1 == "project" && $2 == p { print $3; exit }' "$STATE_FILE"; }

apply_patches() {
    local want p head base rec
    log "Checking DUO-DE patches"
    prepare_patch_root
    want="$(patchset_hash)"

    # The fast path never applies to an explicit reset.
    if ! $RESET_PATCHES \
        && [ "$(state_get patchset || true)" == "$want" ] \
        && [ "$(state_get gapps || true)" == "$GAPPS_PRESENT" ]; then
        local intact=true
        for p in "${PROJECT_PATHS[@]}"; do
            # A network-only sync can move refs/remotes/m/* without a checkout;
            # then the recorded patches sit on an outdated base: re-apply.
            if am_in_progress "$p" \
                || [ "$(proj_head "$p")" != "$(state_head "$p" || true)" ] \
                || [ "$(proj_base "$p" 2>/dev/null || true)" != "$(state_base "$p" || true)" ]; then
                intact=false; break
            fi
        done
        if $intact; then
            log "Patches already applied (patch set $want); keeping the tree as is"
            PATCH_STATUS="already applied"
            return
        fi
    fi

    duo_prepare_history "$UNSHALLOW" "${PROJECT_PATHS[@]}"

    log "Preparing ${#PROJECT_PATHS[@]} projects"
    for p in "${PROJECT_PATHS[@]}"; do
        if am_in_progress "$p"; then
            $RESET_PATCHES || die "$p has an unfinished 'git am'; fix it or rerun with --reset-patches"
            (cd -- "$p" && git am --abort) || true
        fi
        head="$(proj_head "$p")"
        base="$(proj_base "$p")"
        rec="$(state_head "$p" || true)"
        if $RESET_PATCHES; then
            git -C "$p" checkout -q -f --detach "$base"
        elif [ "$head" == "$base" ]; then
            git -C "$p" diff --cached --quiet || die "$p has staged changes; commit/stash them or use --reset-patches"
        elif [ -n "$rec" ] && [ "$head" == "$rec" ]; then
            git -C "$p" checkout -q --detach "$base" \
                || die "$p: cannot return to $base (local changes?); commit/stash them or use --reset-patches"
        else
            die "$p is at $head, which is neither its manifest revision ($base) nor a recorded patched state; commit/stash your work or use --reset-patches"
        fi
    done

    rm -f -- "$STATE_FILE"
    local set
    for set in "${DUO_PATCH_SETS[@]}"; do
        log "Applying $set patches"
        if ! (export LC_ALL=C \
                     GIT_COMMITTER_NAME="DUO-DE dev build" \
                     GIT_COMMITTER_EMAIL="duo-dev@localhost" \
                     GIT_COMMITTER_DATE="$HARNESS_COMMIT_DATE"
              bash "$HARNESS/patch.sh" "$PATCH_ROOT" "$set"); then
            for p in "${PROJECT_PATHS[@]}"; do
                am_in_progress "$p" && warn "unfinished 'git am' in $p"
            done
            die "patch set '$set' failed to apply (see above). If git am could not build a fake ancestor, rerun with --reset-patches --unshallow (full history and base tags); otherwise fix the patch (dev/check-apply.sh lists all failures)"
        fi
    done

    {
        echo "patchset $want"
        echo "gapps $GAPPS_PRESENT"
        echo "harness $HARNESS_COMMIT"
        for p in "${PROJECT_PATHS[@]}"; do
            echo "project $p $(proj_base "$p") $(proj_head "$p")"
        done
    } > "$STATE_FILE.tmp"
    mv -- "$STATE_FILE.tmp" "$STATE_FILE"
    PATCH_STATUS="applied"
    log "Patches applied (patch set $want)"
}

# An interrupted `repo sync` checkout can leave a project with every tracked
# file staged for deletion; Soong then fails with undefined modules. Check that
# no project outside the patch targets (handled by apply_patches) has an index
# differing from HEAD. Fast: one `git diff --cached --quiet` per project.
check_checkouts() {
    local p bad=() refuse=()
    # shellcheck disable=SC2016  # expanded by the sh -c below
    local check_one='git -C "$1" diff --cached --quiet --ignore-submodules >/dev/null 2>&1 || printf "%s\n" "$1"'
    if [ ! -f .repo/project.list ]; then
        warn "no .repo/project.list; skipping the checkout integrity check"
        return 0
    fi
    prepare_patch_root
    log "Checking checkouts of $(grep -c . .repo/project.list) projects"
    while IFS= read -r p; do
        [ -n "$p" ] && bad+=("$p")
    done < <(while IFS= read -r p; do
                 [ -n "$p" ] && [ -z "${SEEN[$p]:-}" ] && printf '%s\0' "$p"
             done < .repo/project.list \
             | xargs -0 -r -n1 -P"$JOBS" sh -c "$check_one" _ \
             | LC_ALL=C sort)
    [ ${#bad[@]} -gt 0 ] || return 0

    printf '  %s\n' "${bad[@]}" >&2
    if ! $REPAIR_CHECKOUTS; then
        die "${#bad[@]} projects have staged changes or a broken checkout (often an interrupted repo sync: every file staged for deletion). Repair with: git -C <project> reset --hard HEAD, or rerun with --repair-checkouts (only for projects without local commits)"
    fi
    for p in "${bad[@]}"; do
        if [ ! -e "$p/.git" ] || [ "$(proj_head "$p" || true)" != "$(proj_base "$p" 2>/dev/null || true)" ]; then
            refuse+=("$p")
            continue
        fi
        log "  repairing $p (git reset --hard HEAD)"
        git -C "$p" reset -q --hard HEAD
        git -C "$p" diff --cached --quiet --ignore-submodules || refuse+=("$p")
    done
    if [ ${#refuse[@]} -gt 0 ]; then
        printf '  %s\n' "${refuse[@]}" >&2
        die "not repairing ${#refuse[@]} projects: missing, local commits (HEAD differs from the manifest revision) or still inconsistent; fix them by hand or re-run dev/sync.sh"
    fi
}
check_checkouts

PATCH_STATUS="skipped (--skip-patches; tree used as is)"
if $SKIP_PATCHES; then
    warn "--skip-patches: not checking or applying patches"
    prepare_patch_root   # only to know which projects to watch
else
    apply_patches
fi

# Pinned DUO-DE source projects (build/dev/pinned.xml) and the full set of
# projects whose state is monitored: patched ones plus pinned ones present.
declare -A PINS=()
declare -a PIN_PATHS=() WATCH_PATHS=("${PROJECT_PATHS[@]}")
while read -r p pin; do
    PINS[$p]="$pin"
    PIN_PATHS+=("$p")
    if [ -d "$p" ] && [ -z "${SEEN[$p]:-}" ]; then WATCH_PATHS+=("$p"); fi
done < <(python3 - "$HARNESS/build/dev/pinned.xml" <<'PY'
import sys
import xml.etree.ElementTree as ET
for p in ET.parse(sys.argv[1]).getroot().iter("project"):
    print(p.get("path"), p.get("revision"))
PY
)

# ---------------------------------------------------------------- DuoProbe (dev only)

# --with-probe copies the probe app into the tree and names it only in the
# untracked duo-dev.mk; release makefiles (build/aosp.mk) never mention it.
# packages/apps/DuoProbe belongs to no repo project, so the copy carries a
# marker file, and a build without --with-probe removes it again. A directory
# there without the marker is not ours and is never touched.
PROBE_SRC="$HARNESS/tools/duo-probe/DuoProbe"
PROBE_DST="packages/apps/DuoProbe"
PROBE_MARKER=".duo-dev-managed"
PROBE_STATUS="not included"
PROBE_SHA256="none"
probe_hash() {
    (cd -- "$1" && LC_ALL=C find . -type f ! -name "$PROBE_MARKER" -print0 | LC_ALL=C sort -z \
        | xargs -0r sha256sum) | sha256sum | cut -d' ' -f1
}
stage_probe() {
    if [ -e "$PROBE_DST" ] && [ ! -f "$PROBE_DST/$PROBE_MARKER" ]; then
        die "$PROBE_DST exists and was not created by dev/build.sh --with-probe; move it out of the tree"
    fi
    if ! $WITH_PROBE; then
        if [ -e "$PROBE_DST" ]; then
            log "Removing $PROBE_DST (no --with-probe)"
            rm -rf -- "$PROBE_DST"
        fi
        return
    fi
    [ -f "$PROBE_SRC/Android.bp" ] || die "$PROBE_SRC/Android.bp not found"
    PROBE_SHA256="$(probe_hash "$PROBE_SRC")"
    # Only replace a changed copy, so an unchanged probe keeps its timestamps.
    if [ ! -d "$PROBE_DST" ] || [ "$(probe_hash "$PROBE_DST")" != "$PROBE_SHA256" ]; then
        log "Copying DuoProbe to $PROBE_DST (dev build only)"
        rm -rf -- "$PROBE_DST" "$PROBE_DST.tmp"
        mkdir -p -- "$(dirname -- "$PROBE_DST")"
        cp -R -- "$PROBE_SRC" "$PROBE_DST.tmp"
        echo "Copied by DUO-DE dev/build.sh --with-probe from $HARNESS_COMMIT; removed by a build without it." \
            > "$PROBE_DST.tmp/$PROBE_MARKER"
        mv -- "$PROBE_DST.tmp" "$PROBE_DST"
    fi
    PROBE_STATUS="included ($PROBE_DST; PRODUCT_PACKAGES += DuoProbe in $DEV_MK only)"
}

# ---------------------------------------------------------------- makefiles

# Same content as build/aosp.mk (which build.sh copies over device/phh/treble/aosp.mk),
# with the updater URL replaced. Kept in a separate untracked file so that the
# device/phh/treble project stays clean.
DEV_MK="device/phh/treble/duo-dev.mk"
stage_probe
generate_makefiles() {
    local tmp f
    log "Generating product makefiles (updater feed: $OTA_URL)"
    grep -Eq '^[[:space:]]*ro\.system\.ota\.json_url=' "$HARNESS/build/aosp.mk" \
        || die "build/aosp.mk no longer sets ro.system.ota.json_url; update dev/build.sh"
    tmp="$(mktemp -d)"
    {
        echo "# Generated by dev/build.sh from build/aosp.mk. Do not edit."
        sed -E "s#^([[:space:]]*)ro\.system\.ota\.json_url=.*#\1ro.system.ota.json_url=${OTA_URL}#" \
            "$HARNESS/build/aosp.mk"
        echo
        echo
        echo "# DUO-DE developer build marker"
        echo "PRODUCT_SYSTEM_DEFAULT_PROPERTIES += \\"
        echo "    ro.duode.dev_build=true"
        if $WITH_PROBE; then
            echo
            echo "# DEV ONLY (dev/build.sh --with-probe): DuoProbe feasibility probe app"
            echo "PRODUCT_PACKAGES += DuoProbe"
            echo "PRODUCT_SYSTEM_DEFAULT_PROPERTIES += \\"
            echo "    ro.duode.dev_probe=true"
        fi
    } > "$tmp/duo-dev.mk"
    (cd -- "$tmp" && bash "$TREE/device/phh/treble/generate.sh" "$DEV_MK" >/dev/null)
    # Only replace changed files, so unchanged makefiles keep their timestamps.
    for f in "$tmp"/*; do
        if [ "$(basename -- "$f")" == duo-dev.mk ]; then
            cmp -s "$f" "$DEV_MK" || cp -- "$f" "$DEV_MK"
        else
            cmp -s "$f" "device/phh/treble/$(basename -- "$f")" || cp -- "$f" device/phh/treble/
        fi
    done
    rm -rf -- "$tmp"
    grep -q "$DEV_MK" "device/phh/treble/$DUO_LUNCH_PRODUCT.mk" \
        || die "generated $DUO_LUNCH_PRODUCT.mk does not inherit $DEV_MK"
}
generate_makefiles

if $PATCH_ONLY; then
    log "--patch-only: tree prepared, not building"
    exit 0
fi

# ---------------------------------------------------------------- TrebleApp

TREBLE_APP_STATUS="not rebuilt (--skip-treble-app; prebuilt from vendor/hardware_overlay)"
if ! $SKIP_TREBLE_APP; then
    duo_require_cmds java
    log "Building TrebleApp (Gradle; needs network on first run)"
    export GRADLE_USER_HOME="${GRADLE_USER_HOME:-$STATE_DIR/gradle}"
    (cd treble_app && bash build.sh release)
    install -m 0644 treble_app/TrebleApp.apk vendor/hardware_overlay/TrebleApp/app.apk
    TREBLE_APP_STATUS="rebuilt from treble_app"
fi
TREBLE_APP_SHA256="$(sha256sum vendor/hardware_overlay/TrebleApp/app.apk | cut -d' ' -f1)"

# ---------------------------------------------------------------- WebView source

# build/dev/pinned.xml replaces AOSP's platform/external/chromium-webview with
# LineageOS's prebuilt (Git LFS) and links its Android.bp from the patches
# project. A tree synced before that, or a failed LFS download (the file is then
# the small pointer text), must not silently build the wrong WebView.
WEBVIEW_DIR="external/chromium-webview"
WEBVIEW_PREBUILT="$WEBVIEW_DIR/prebuilt/arm64"
AAPT2="prebuilts/sdk/tools/linux/bin/aapt2"
check_webview_source() {
    local pointer oid
    [ ! -e "$WEBVIEW_DIR/.git" ] \
        || die "$WEBVIEW_DIR is still AOSP's platform/external/chromium-webview project; build/dev/pinned.xml replaces it with LineageOS's WebView. Re-run dev/sync.sh (if repo keeps the old directory, move $WEBVIEW_DIR out of the tree and sync again)"
    grep -q 'prebuilt/arm64/webview.apk' "$WEBVIEW_DIR/Android.bp" 2>/dev/null \
        || die "$WEBVIEW_DIR/Android.bp is not LineageOS's os_pickup.bp (missing repo linkfile); re-run dev/sync.sh"
    [ -f "$WEBVIEW_PREBUILT/webview.apk" ] || die "$WEBVIEW_PREBUILT/webview.apk is missing; re-run dev/sync.sh"
    pointer="$(git -C "$WEBVIEW_PREBUILT" cat-file blob HEAD:webview.apk)" \
        || die "cannot read webview.apk from $WEBVIEW_PREBUILT HEAD"
    oid="$(sed -n 's/^oid sha256:\([0-9a-f]\{64\}\)$/\1/p' <<<"$pointer")"
    [ -n "$oid" ] || die "$WEBVIEW_PREBUILT/webview.apk is not a Git LFS file at HEAD (unexpected upstream change)"
    WEBVIEW_SRC_SHA256="$(sha256sum "$WEBVIEW_PREBUILT/webview.apk" | cut -d' ' -f1)"
    [ "$WEBVIEW_SRC_SHA256" == "$oid" ] \
        || die "$WEBVIEW_PREBUILT/webview.apk does not match its Git LFS object $oid (an LFS pointer that was never downloaded?). Run: git -C $WEBVIEW_PREBUILT lfs pull"
    WEBVIEW_SRC_COMMIT="$(proj_head "$WEBVIEW_PREBUILT")"
    WEBVIEW_CHROMIUM="$(sed -n 's/^android_default_version_name = "\([0-9.]*\)"$/\1/p' "$WEBVIEW_DIR/README" 2>/dev/null | head -n1)"
    [ -n "$WEBVIEW_CHROMIUM" ] || die "cannot read the Chromium version from $WEBVIEW_DIR/README"
    [ -x "$AAPT2" ] || die "$AAPT2 not found (needed to check the built WebView and overlay)"
    log "WebView source: LineageOS prebuilt $WEBVIEW_SRC_COMMIT, Chromium $WEBVIEW_CHROMIUM, apk sha256 $WEBVIEW_SRC_SHA256"
}
check_webview_source

# State of every watched project (HEAD, tracked changes, and name/size/mtime of
# untracked files that are not ignored), the generated product makefiles, the
# TrebleApp prebuilt and the local manifests. Ignored files (build outputs) are
# not included. Taken before the build and compared again before collecting
# artifacts, so provenance always describes the built tree.
GEN_MAKEFILES_GLOB=(device/phh/treble/duo-dev.mk device/phh/treble/AndroidProducts.mk device/phh/treble/treble_*.mk)
untracked_state() {
    git -C "$1" ls-files -z --others --exclude-standard \
        | (cd -- "$1" && xargs -0r stat -c '%n %s %Y') | sha256sum | cut -d' ' -f1
}
tree_snapshot() {
    local p
    for p in "${WATCH_PATHS[@]}"; do
        printf 'project %s %s %s %s\n' "$p" "$(proj_head "$p")" \
            "$(git -C "$p" diff HEAD --no-ext-diff --binary | sha256sum | cut -d' ' -f1)" \
            "$(untracked_state "$p")"
    done
    # shellcheck disable=SC2068
    sha256sum ${GEN_MAKEFILES_GLOB[@]} | sed 's/^/makefile /'
    printf 'trebleapp %s\n' "$(sha256sum vendor/hardware_overlay/TrebleApp/app.apk | cut -d' ' -f1)"
    printf 'local_manifests %s\n' "$(cat .repo/local_manifests/*.xml 2>/dev/null | sha256sum | cut -d' ' -f1)"
    if [ -d "$PROBE_DST" ]; then printf 'probe %s\n' "$(probe_hash "$PROBE_DST")"; else echo "probe none"; fi
}
# Projects with local changes, ignoring the files this script manages itself.
dirty_projects() {
    local p out=()
    local managed=(':(exclude)duo-dev.mk' ':(exclude)TrebleApp/app.apk' ':(exclude)TrebleApp.apk')
    for p in "${WATCH_PATHS[@]}"; do
        if ! git -C "$p" diff HEAD --quiet --no-ext-diff -- . "${managed[@]}" \
            || [ -n "$(git -C "$p" ls-files --others --exclude-standard -- . "${managed[@]}")" ]; then
            out+=("$p")
        fi
    done
    echo "${out[*]:-none}"
}
TREE_SNAPSHOT_BEFORE="$(tree_snapshot)"
DIRTY_PROJECTS="$(dirty_projects)"
[ "$DIRTY_PROJECTS" == none ] || warn "building with uncommitted changes in: $DIRTY_PROJECTS"

# ---------------------------------------------------------------- build

export BUILD_NUMBER="$BUILD_TAG"
log "Setting up $DUO_LUNCH_TARGET"
set +eu
# shellcheck disable=SC1091
source build/envsetup.sh >/dev/null
lunch "$DUO_LUNCH_TARGET"
rc=$?
DEV_CERT="$(get_build_var PRODUCT_DEFAULT_DEV_CERTIFICATE)"
set -eu
[ "$rc" -eq 0 ] || die "lunch $DUO_LUNCH_TARGET failed"
[ -n "${OUT:-}" ] || die "lunch did not set \$OUT"

case "$DEV_CERT" in
    ""|build/make/target/product/security/testkey) ;;
    *) die "PRODUCT_DEFAULT_DEV_CERTIFICATE is '$DEV_CERT', expected the AOSP test key" ;;
esac

set +u
if $INSTALLCLEAN; then
    log "make installclean"
    make -j"$JOBS" installclean
fi
log "make systemimage (-j$JOBS)"
make -j"$JOBS" systemimage
set -u

# ---------------------------------------------------------------- verify

PROP_FILES=("$OUT/system/build.prop" "$OUT/system/etc/prop.default")
prop() {
    local f v
    for f in "${PROP_FILES[@]}"; do
        [ -f "$f" ] || continue
        v="$(grep -m1 "^$1=" "$f" | cut -d= -f2- || true)"
        [ -z "$v" ] || { printf '%s\n' "$v"; return; }
    done
}

[ -f "$OUT/system.img" ] || die "$OUT/system.img was not produced"
FINGERPRINT="$(prop ro.system.build.fingerprint)"
[ -n "$FINGERPRINT" ] || FINGERPRINT="$(prop ro.build.fingerprint)"
BUILD_TAGS="$(prop ro.system.build.tags)"
[ -n "$BUILD_TAGS" ] || BUILD_TAGS="$(prop ro.build.tags)"
IMG_OTA_URL="$(prop ro.system.ota.json_url)"

[ "$BUILD_TAGS" == test-keys ] || die "build tags are '$BUILD_TAGS', expected test-keys"
[ "$IMG_OTA_URL" == "$OTA_URL" ] || die "image updater feed is '$IMG_OTA_URL', expected '$OTA_URL'"
[[ "$FINGERPRINT" == *"$BUILD_TAG"* ]] || die "fingerprint '$FINGERPRINT' does not contain $BUILD_TAG"
if grep -qi archfx "${PROP_FILES[0]}"; then
    die "system build.prop still references Archfx"
fi
PROBE_APK="$OUT/system/app/DuoProbe/DuoProbe.apk"
if $WITH_PROBE; then
    [ -f "$PROBE_APK" ] || die "--with-probe: $PROBE_APK was not produced"
    PROBE_STATUS+="; apk_sha256 $(sha256sum "$PROBE_APK" | cut -d' ' -f1)"
elif [ -e "$OUT/system/app/DuoProbe" ]; then
    die "$OUT/system/app/DuoProbe is left over from a --with-probe build; rebuild without --no-installclean"
fi

# Base image (vanilla): LineageOS WebView, its default-provider overlay, Jelly.
PRODUCT_APPS="$OUT/system/product/app"
WEBVIEW_APK="$PRODUCT_APPS/webview/webview.apk"
JELLY_APK="$PRODUCT_APPS/Jelly/Jelly.apk"
[ -f "$WEBVIEW_APK" ] || die "$WEBVIEW_APK was not produced"
[ -f "$JELLY_APK" ] || die "$JELLY_APK was not produced (vanilla products add Jelly via device/phh/treble/duo-vanilla.mk)"
WEBVIEW_OVERLAY_APK="$(find "$OUT/system/product/overlay" -name DuoVanillaWebViewOverlay.apk -print -quit 2>/dev/null || true)"
[ -n "$WEBVIEW_OVERLAY_APK" ] || die "DuoVanillaWebViewOverlay.apk was not produced under $OUT/system/product/overlay"
badging="$("$AAPT2" dump badging "$WEBVIEW_APK" 2>/dev/null | head -n1 || true)"
WEBVIEW_BUILT_PKG="$(sed -n "s/^package: name='\([^']*\)'.*/\1/p" <<<"$badging")"
WEBVIEW_BUILT_VERSION="$(sed -n "s/.* versionName='\([^']*\)'.*/\1/p" <<<"$badging")"
[ "$WEBVIEW_BUILT_PKG" == com.android.webview ] \
    || die "built WebView package is '$WEBVIEW_BUILT_PKG' (aapt2 dump badging), expected com.android.webview"
[ "$WEBVIEW_BUILT_VERSION" == "$WEBVIEW_CHROMIUM" ] \
    || die "built WebView versionName is '$WEBVIEW_BUILT_VERSION', expected Chromium $WEBVIEW_CHROMIUM from $WEBVIEW_DIR/README"
# Informational: the first provider in the overlay is the default (WebViewUpdateServiceImpl2).
WEBVIEW_OVERLAY_FIRST="$("$AAPT2" dump xmltree --file res/xml/config_webview_packages.xml "$WEBVIEW_OVERLAY_APK" 2>/dev/null \
    | sed -n 's/.*A: packageName="\([^"]*\)".*/\1/p' | head -n1 || true)"
if [ "$WEBVIEW_OVERLAY_FIRST" != com.android.webview ]; then
    warn "could not confirm that $WEBVIEW_OVERLAY_APK lists com.android.webview first (got '${WEBVIEW_OVERLAY_FIRST:-nothing}'); check on the device with dumpsys webviewupdate"
    WEBVIEW_OVERLAY_FIRST="unconfirmed (${WEBVIEW_OVERLAY_FIRST:-not read})"
fi
if [ -e "$PRODUCT_APPS/Browser2" ]; then
    warn "$PRODUCT_APPS/Browser2 is still installed (Jelly overrides it; stale with --no-installclean?)"
fi

# The tree must not have changed while building (e.g. a concurrent sync).
TREE_SNAPSHOT_AFTER="$(tree_snapshot)"
if [ "$TREE_SNAPSHOT_AFTER" != "$TREE_SNAPSHOT_BEFORE" ]; then
    diff <(echo "$TREE_SNAPSHOT_BEFORE") <(echo "$TREE_SNAPSHOT_AFTER") >&2 || true
    die "the tree changed during the build; not writing artifacts. Rebuild."
fi
if ! $SKIP_PATCHES; then
    for p in "${PROJECT_PATHS[@]}"; do
        [ "$(proj_head "$p")" == "$(state_head "$p" || true)" ] \
            || die "$p is no longer at its recorded patched HEAD; not writing artifacts. Rebuild."
    done
fi

# ---------------------------------------------------------------- artifacts

ART="$ARTIFACTS_DIR/$BUILD_TAG"
[ ! -e "$ART" ] || die "$ART already exists"
mkdir -p -- "$ART"
IMG_NAME="system-$DUO_LUNCH_PRODUCT-$BUILD_TAG.img"
log "Collecting artifacts in $ART"
cp --reflink=auto -- "$OUT/system.img" "$ART/$IMG_NAME"
cp -- "$OUT/system/build.prop" "$ART/system-build.prop"
duo_repo manifest -r -o "$ART/manifest.xml"
[ ! -f "$STATE_FILE" ] || cp -- "$STATE_FILE" "$ART/patch-state.txt"
(cd -- "$ART" && sha256sum -- "$IMG_NAME" > "$IMG_NAME.sha256")
IMG_SHA256="$(cut -d' ' -f1 "$ART/$IMG_NAME.sha256")"

# Does the built source match build/dev/pinned.xml?
# - patched projects: the base recorded when the patches were applied;
# - other projects: the checked-out HEAD.
# The live refs/remotes/m/* ref is only reported: repo updates it on fetch,
# before (or without) a checkout. manifest.xml records post-patch HEADs.
bad_pins=()
{
    echo "# path kind pinned checked(compared) live_m_ref head(built)"
    for p in "${PIN_PATHS[@]}"; do
        pin="${PINS[$p]}"
        if [ ! -d "$p" ]; then
            kind=missing; checked=missing; live=missing; head=missing
        else
            head="$(proj_head "$p" 2>/dev/null)" || head=unknown
            live="$(proj_base "$p" 2>/dev/null)" || live=unknown
            if [ -n "${SEEN[$p]:-}" ]; then
                kind=patched
                if [ "$head" == "$(state_head "$p" || true)" ]; then
                    checked="$(state_base "$p" || true)"
                else
                    checked=unknown   # --skip-patches with a tree not matching patch-state
                fi
                [ -n "$checked" ] || checked=unknown
            else
                kind=unpatched
                checked="$head"
            fi
        fi
        [ "$checked" == "$pin" ] || bad_pins+=("$p")
        echo "$p $kind $pin $checked $live $head"
    done
} > "$ART/pinned-bases.txt"
if [ ${#bad_pins[@]} -eq 0 ]; then
    PIN_CHECK="match (patched bases and unpatched HEADs equal build/dev/pinned.xml)"
else
    PIN_CHECK="MISMATCH (${#bad_pins[@]} projects): ${bad_pins[*]}"
    warn "tree bases differ from build/dev/pinned.xml: $PIN_CHECK"
fi

first_line() { "$@" 2>&1 | head -n1 || true; }
local_manifest_sums="$(cd .repo/local_manifests && sha256sum -- *.xml 2>/dev/null | sed 's/^/    /' || true)"

cat > "$ART/provenance.txt" <<EOF
# DUO-DE developer build provenance
build_id: $BUILD_TAG
created_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)
lunch_target: $DUO_LUNCH_TARGET
image: $IMG_NAME
image_sha256: $IMG_SHA256
image_bytes: $(stat -c %s "$ART/$IMG_NAME")
fingerprint: $FINGERPRINT
build_tags: $BUILD_TAGS
signing: AOSP test keys (PRODUCT_DEFAULT_DEV_CERTIFICATE=${DEV_CERT:-default}); no release keys
ota_json_url: $IMG_OTA_URL

harness_repo: $(git -C "$HARNESS" remote get-url origin 2>/dev/null || echo unknown)
harness_commit: $HARNESS_COMMIT
harness_dirty: $HARNESS_DIRTY
harness_branch: $(git -C "$HARNESS" rev-parse --abbrev-ref HEAD)
patch_sets: ${DUO_PATCH_SETS[*]}
patch_status: $PATCH_STATUS
patchset_sha256: $(state_get patchset || echo unknown)
gapps_project_present: $GAPPS_PRESENT
treble_app: $TREBLE_APP_STATUS
treble_app_apk_sha256: $TREBLE_APP_SHA256
projects_with_uncommitted_changes: $DIRTY_PROJECTS
duo_probe: $PROBE_STATUS
duo_probe_source_sha256: $PROBE_SHA256
webview: LineageOS prebuilt ($WEBVIEW_PREBUILT @ $WEBVIEW_SRC_COMMIT), package $WEBVIEW_BUILT_PKG
webview_chromium_version: $WEBVIEW_CHROMIUM (README; built apk versionName $WEBVIEW_BUILT_VERSION)
webview_source_apk_sha256: $WEBVIEW_SRC_SHA256 (equals its Git LFS object id)
webview_installed_apk_sha256: $(sha256sum "$WEBVIEW_APK" | cut -d' ' -f1) (re-signed with the build's certificate)
webview_default_provider_overlay: ${WEBVIEW_OVERLAY_APK#"$OUT"/}; first provider $WEBVIEW_OVERLAY_FIRST
jelly_apk_sha256: $(sha256sum "$JELLY_APK" | cut -d' ' -f1)
tree_unchanged_during_build: verified

aosp_manifest_branch: $(git config --file .repo/manifests.git/config --get branch.default.merge || echo unknown)
aosp_manifest_commit: $(git -C .repo/manifests rev-parse HEAD 2>/dev/null || echo unknown)
pinned_manifest_sha256: $(sha256sum "$HARNESS/build/dev/pinned.xml" | cut -d' ' -f1)
pinned_manifest_check: $PIN_CHECK
pinned_bases: pinned-bases.txt
local_manifests:
$local_manifest_sums
resolved_manifest: manifest.xml (repo manifest -r; revisions are the built, post-patch HEADs)
resolved_manifest_sha256: $(sha256sum "$ART/manifest.xml" | cut -d' ' -f1)

container_image: ${DUO_DEV_CONTAINER_IMAGE:-none (not run through dev/podman.sh)}
container_image_id: ${DUO_DEV_CONTAINER_IMAGE_ID:-none}
containerfile_sha256: ${DUO_DEV_CONTAINERFILE_SHA256:-none}
os: $(sed -n 's/^PRETTY_NAME="\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' /etc/os-release 2>/dev/null || echo unknown)
kernel: $(uname -sr)
java: $(first_line java -version)
git: $(git --version)
repo: $(first_line duo_repo --version)
jobs: $JOBS
installclean: $INSTALLCLEAN
EOF

(cd -- "$ART" && sha256sum -- * | grep -v ' SHA256SUMS$' > SHA256SUMS)

log "Done: $ART/$IMG_NAME"
log "sha256: $IMG_SHA256"
