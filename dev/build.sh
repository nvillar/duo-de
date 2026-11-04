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
TREE_ARG=""

usage() { sed -n '2,23p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

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
flock -n 9 || die "another dev/build.sh is running on $TREE"

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

# Build a filtered view of patches/ (symlinks) and compute the patch-set hash.
prepare_patch_root() {
    local set dir proj path
    rm -rf -- "$PATCH_ROOT"
    for set in "${DUO_PATCH_SETS[@]}"; do
        mkdir -p "$PATCH_ROOT/patches/$set"
        for dir in "$HARNESS/patches/$set"/*/; do
            proj="$(basename -- "$dir")"
            path="$(duo_project_path "$proj")"
            if [ ! -d "$path" ]; then
                if [ "$path" == vendor/gapps ]; then
                    log "  $set/$proj: vendor/gapps not in tree, skipping (vanilla build)"
                    continue
                fi
                die "patch project $set/$proj maps to '$path', which is missing from the tree"
            fi
            ln -s -- "${dir%/}" "$PATCH_ROOT/patches/$set/$proj"
            if [ -z "${SEEN[$path]:-}" ]; then
                SEEN[$path]=1
                PROJECT_PATHS+=("$path")
            fi
        done
    done
}

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

proj_head() { git -C "$1" rev-parse --verify -q HEAD; }

# Commit the manifest checked out for a project (repo's refs/remotes/m/<branch>).
proj_base() {
    local refs ref
    refs="$(git -C "$1" for-each-ref --format='%(refname)' refs/remotes/m/)"
    if [ "$(grep -c . <<<"$refs")" -eq 1 ]; then
        ref="$refs"
    elif grep -qx "refs/remotes/m/$DUO_AOSP_BRANCH" <<<"$refs"; then
        ref="refs/remotes/m/$DUO_AOSP_BRANCH"
    else
        die "$1: cannot determine the manifest revision (refs/remotes/m/*: ${refs:-none})"
    fi
    git -C "$1" rev-parse --verify -q "$ref^{commit}" || die "$1: cannot resolve $ref"
}

am_in_progress() { (cd -- "$1" && [ -d "$(git rev-parse --git-path rebase-apply)" ]); }

state_get() { [ -f "$STATE_FILE" ] && awk -v k="$1" '$1 == k { print $2; exit }' "$STATE_FILE"; }
state_head() { [ -f "$STATE_FILE" ] && awk -v p="$1" '$1 == "project" && $2 == p { print $4; exit }' "$STATE_FILE"; }

apply_patches() {
    local want p head base rec
    log "Checking DUO-DE patches"
    prepare_patch_root
    want="$(patchset_hash)"

    if [ "$(state_get patchset || true)" == "$want" ] && [ "$(state_get gapps || true)" == "$GAPPS_PRESENT" ]; then
        local intact=true
        for p in "${PROJECT_PATHS[@]}"; do
            if am_in_progress "$p" || [ "$(proj_head "$p")" != "$(state_head "$p" || true)" ]; then
                intact=false; break
            fi
        done
        if $intact; then
            log "Patches already applied (patch set $want); keeping the tree as is"
            PATCH_STATUS="already applied"
            return
        fi
    fi

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
            die "patch set '$set' failed to apply (see above). Fix the patch, then rerun with --reset-patches"
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

PATCH_STATUS="skipped (--skip-patches; tree used as is)"
if $SKIP_PATCHES; then
    warn "--skip-patches: not checking or applying patches"
else
    apply_patches
fi

# ---------------------------------------------------------------- makefiles

# Same content as build/aosp.mk (which build.sh copies over device/phh/treble/aosp.mk),
# with the updater URL replaced. Kept in a separate untracked file so that the
# device/phh/treble project stays clean.
DEV_MK="device/phh/treble/duo-dev.mk"
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

# ---------------------------------------------------------------- artifacts

ART="$ARTIFACTS_DIR/$BUILD_TAG"
[ ! -e "$ART" ] || die "$ART already exists"
mkdir -p -- "$ART"
IMG_NAME="system-$DUO_LUNCH_PRODUCT-$BUILD_TAG.img"
log "Collecting artifacts in $ART"
cp --reflink=auto -- "$OUT/system.img" "$ART/$IMG_NAME"
cp -- "$OUT/system/build.prop" "$ART/system-build.prop"
repo manifest -r -o "$ART/manifest.xml"
[ ! -f "$STATE_FILE" ] || cp -- "$STATE_FILE" "$ART/patch-state.txt"
(cd -- "$ART" && sha256sum -- "$IMG_NAME" > "$IMG_NAME.sha256")
IMG_SHA256="$(cut -d' ' -f1 "$ART/$IMG_NAME.sha256")"

# Do the resolved revisions match build/dev/pinned.xml?
PIN_CHECK="$(python3 - "$HARNESS/build/dev/pinned.xml" "$ART/manifest.xml" <<'PY'
import sys
import xml.etree.ElementTree as ET
pinned = {p.get("path"): p.get("revision") for p in ET.parse(sys.argv[1]).getroot().iter("project")}
actual = {(p.get("path") or p.get("name")): p.get("revision") for p in ET.parse(sys.argv[2]).getroot().iter("project")}
bad = [path for path, rev in sorted(pinned.items()) if actual.get(path) != rev]
print("match" if not bad else f"MISMATCH ({len(bad)} projects): " + ", ".join(bad))
PY
)"
[ "$PIN_CHECK" == match ] || warn "tree differs from build/dev/pinned.xml: $PIN_CHECK"

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

aosp_manifest_branch: $(git config --file .repo/manifests.git/config --get branch.default.merge || echo unknown)
aosp_manifest_commit: $(git -C .repo/manifests rev-parse HEAD 2>/dev/null || echo unknown)
pinned_manifest_sha256: $(sha256sum "$HARNESS/build/dev/pinned.xml" | cut -d' ' -f1)
pinned_manifest_check: $PIN_CHECK
local_manifests:
$local_manifest_sums
resolved_manifest: manifest.xml (repo manifest -r)
resolved_manifest_sha256: $(sha256sum "$ART/manifest.xml" | cut -d' ' -f1)

container_image: ${DUO_DEV_CONTAINER_IMAGE:-none (not run through dev/podman.sh)}
container_image_id: ${DUO_DEV_CONTAINER_IMAGE_ID:-none}
containerfile_sha256: ${DUO_DEV_CONTAINERFILE_SHA256:-none}
os: $(sed -n 's/^PRETTY_NAME="\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' /etc/os-release 2>/dev/null || echo unknown)
kernel: $(uname -sr)
java: $(first_line java -version)
git: $(git --version)
repo: $(first_line repo --version)
jobs: $JOBS
installclean: $INSTALLCLEAN
EOF

(cd -- "$ART" && sha256sum -- * | grep -v ' SHA256SUMS$' > SHA256SUMS)

log "Done: $ART/$IMG_NAME"
log "sha256: $IMG_SHA256"
