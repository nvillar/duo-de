# Shared helpers for the DUO-DE developer scripts. Source, do not execute.
# shellcheck shell=bash disable=SC2034

DUO_AOSP_BRANCH="android-16.0.0_r4"
DUO_AOSP_MANIFEST_URL="https://android.googlesource.com/platform/manifest"
DUO_LUNCH_PRODUCT="treble_arm64_bvN"
DUO_LUNCH_TARGET="${DUO_LUNCH_PRODUCT}-bp2a-userdebug"
# Patch sets in the order build.sh / clean-and-clone.sh apply them.
DUO_PATCH_SETS=(trebledroid personal duo)
# Default updater feed for developer builds: a reserved, never-resolving
# domain (RFC 2606), so the in-ROM updater can never fetch an upstream OTA.
DUO_DEFAULT_OTA_URL="https://ota-disabled.invalid/duo-de-dev/ota.json"
# Local manifest file names that dev/sync.sh manages.
DUO_LOCAL_MANIFESTS=(duo-de-pinned.xml duo-de-gapps.xml remove.xml)

log() { printf '==> %s\n' "$*" >&2; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# Root of this harness checkout (the directory holding patch.sh).
duo_harness_root() {
    local here
    here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
    dirname -- "$here"
}

# Resolve and validate an Android tree argument.
duo_tree_path() {
    [ -n "${1:-}" ] || die "missing <aosp-tree> argument"
    [ -d "$1" ] || die "tree '$1' does not exist"
    (cd -- "$1" && pwd -P)
}

# Map a patches/<set>/<project> directory name to its tree path.
# Must stay identical to the mapping in patch.sh.
duo_project_path() {
    local p
    p="$(tr _ / <<<"$1" | sed -e 's;platform/;;g')"
    [ "$p" == build ] && p=build/make
    [ "$p" == treble/app ] && p=treble_app
    [ "$p" == vendor/hardware/overlay ] && p=vendor/hardware_overlay
    printf '%s\n' "$p"
}

duo_require_cmds() {
    local c missing=()
    for c in "$@"; do
        command -v "$c" >/dev/null 2>&1 || missing+=("$c")
    done
    [ ${#missing[@]} -eq 0 ] || die "missing required commands: ${missing[*]}"
}

# Prefer the tree's own repo launcher (.repo/repo/repo, kept current by repo
# itself) over an older system launcher, which only prints a version warning.
duo_repo() {
    if [ -x .repo/repo/repo ]; then
        .repo/repo/repo "$@"
    else
        repo "$@"
    fi
}

duo_jobs_default() {
    nproc --all 2>/dev/null || echo 4
}
