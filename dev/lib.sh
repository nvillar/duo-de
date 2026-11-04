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

# ---------------------------------------------------------------- patches

# Fill PROJECT_PATHS (unique tree paths that receive patches, in first-use
# order) and SEEN[path], and build a filtered view of patches/ under
# $PATCH_ROOT (symlinks) for patch.sh. Callers declare PATCH_ROOT, HARNESS,
# `declare -a PROJECT_PATHS=()` and `declare -A SEEN=()`. Run in the tree root.
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
                # shellcheck disable=SC2004  # SEEN is associative
                SEEN[$path]=1
                PROJECT_PATHS+=("$path")
            fi
        done
    done
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

is_shallow() { [ "$(git -C "$1" rev-parse --is-shallow-repository)" == true ]; }

# ---------------------------------------------------------------- history

# Earlier Android 16 release tags. Several patches were generated on r2-era
# sources: their base blobs are reachable only from these tags, not from the
# r4 branch history, so `git am -3` needs them to build a fake ancestor.
DUO_BASE_TAG_REFSPEC='+refs/tags/android-16.0.0_r*:refs/tags/android-16.0.0_r*'
DUO_BASE_TAGS_DONE=".duo-dev/base-tags-fetched"

proj_remote() { git -C "$1" remote | head -n1; }

# AOSP-hosted project: repo's remote is named "aosp" or points at googlesource.
proj_is_aosp() {
    local r url
    r="$(proj_remote "$1")"
    [ -n "$r" ] || return 1
    [ "$r" == aosp ] && return 0
    url="$(git -C "$1" config --get "remote.$r.url" || true)"
    [[ "$url" == *googlesource.com* ]]
}

base_tags_done() { [ -f "$DUO_BASE_TAGS_DONE" ] && grep -qxF -- "$1" "$DUO_BASE_TAGS_DONE"; }

# Run a git command in project $1 up to 4 times, pausing 30/60/120 s between
# attempts (android.googlesource.com answers HTTP 429 under load).
fetch_retry() {
    local p="$1" what="$2" attempt pause=30
    shift 2
    for ((attempt = 1; ; attempt++)); do
        log "  $p: $what (attempt $attempt/4)"
        git -C "$p" "$@" && return 0
        [ "$attempt" -lt 4 ] || return 1
        warn "$what failed for $p; retrying in ${pause}s"
        sleep "$pause"
        pause=$((pause * 2))
    done
}

# Make sure the patched projects have what `git am -3` needs:
# full history (not a --depth=1 clone) and, for AOSP projects, the earlier
# android-16.0.0_r* tags. Usage: duo_prepare_history <fetch:true|false> <paths...>
# Without fetch, shallow projects are an error and missing tags a warning.
# Run in the tree root, under the tree lock.
duo_prepare_history() {
    local fetch="$1" p remote base shallow=() untagged=()
    shift
    for p in "$@"; do
        if is_shallow "$p"; then shallow+=("$p"); fi
        if proj_is_aosp "$p" && ! base_tags_done "$p"; then untagged+=("$p"); fi
    done
    if [ "$fetch" != true ]; then
        if [ ${#shallow[@]} -gt 0 ]; then
            printf '  %s\n' "${shallow[@]}" >&2
            die "${#shallow[@]} patched projects are shallow clones; rerun with --unshallow (fetches their full history and base tags) or see docs/development-setup.md"
        fi
        if [ ${#untagged[@]} -gt 0 ]; then
            warn "${#untagged[@]} AOSP patched projects lack the android-16.0.0_r* base tags; if a patch fails to apply, rerun with --unshallow"
        fi
        return 0
    fi
    if [ ${#shallow[@]} -gt 0 ]; then
        log "Fetching full history for ${#shallow[@]} shallow patched projects"
        for p in "${shallow[@]}"; do
            remote="$(proj_remote "$p")"
            [ -n "$remote" ] || die "$p has no git remote to unshallow from"
            base="$(proj_base "$p")"
            fetch_retry "$p" "unshallow" fetch -q --unshallow --no-tags "$remote" "$base" \
                || fetch_retry "$p" "unshallow (remote refspec)" fetch -q --unshallow --no-tags "$remote" \
                || die "could not unshallow $p (HTTP 429 rate limiting? wait and rerun)"
            ! is_shallow "$p" || die "$p is still shallow after fetching"
        done
    fi
    if [ ${#untagged[@]} -gt 0 ]; then
        log "Fetching android-16.0.0_r* tags for ${#untagged[@]} AOSP patched projects"
        mkdir -p "$(dirname -- "$DUO_BASE_TAGS_DONE")"
        for p in "${untagged[@]}"; do
            remote="$(proj_remote "$p")"
            fetch_retry "$p" "base tags" fetch -q --no-tags "$remote" "$DUO_BASE_TAG_REFSPEC" \
                || die "could not fetch base tags for $p (HTTP 429 rate limiting? wait and rerun)"
            printf '%s\n' "$p" >> "$DUO_BASE_TAGS_DONE"
        done
    fi
}