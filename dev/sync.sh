#!/bin/bash
# Create or update an Android tree for DUO-DE developer builds, using the
# pinned manifest (build/dev/pinned.xml) and build/remove.xml.
#
# Usage: dev/sync.sh [options] <aosp-tree>
#
#   --depth N        clone depth for a new `repo init` (default 1; 0 = full history)
#   --jobs N         parallel fetch jobs (default 4; android.googlesource.com
#                    answers HTTP 429 when a client fetches too much at once)
#   --attempts N     whole-sync attempts, with growing pauses (default 4)
#   --retry-fetches N  per-project fetch retries inside repo sync (default 5)
#   --with-gapps     also install build/dev/pinned-gapps.xml (vendor/gapps)
#   --no-sync        only install the local manifests, do not run `repo sync`
#
# Only <aosp-tree> is modified. Unknown files in .repo/local_manifests are moved
# to .repo/local_manifests.disabled/, not deleted. No `git clean`, no reset.
# Projects carrying DUO-DE patch commits are moved back to their manifest
# revision by `repo sync`; dev/build.sh re-applies the patches afterwards.
# A clean checkout of AOSP's obsolete external/chromium-webview (replaced by
# LineageOS's WebView) is moved to .duo-dev/obsolete/ before syncing.
# Holds the same tree lock as dev/build.sh, so it never runs during a build.

set -euo pipefail
# shellcheck source=dev/lib.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"

DEPTH=1
JOBS=4
ATTEMPTS=4
RETRY_FETCHES=5
WITH_GAPPS=false
DO_SYNC=true
TREE_ARG=""

usage() { sed -n '2,21p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --depth) DEPTH="${2:?--depth needs a value}"; shift 2 ;;
        --jobs) JOBS="${2:?--jobs needs a value}"; shift 2 ;;
        --attempts) ATTEMPTS="${2:?--attempts needs a value}"; shift 2 ;;
        --retry-fetches) RETRY_FETCHES="${2:?--retry-fetches needs a value}"; shift 2 ;;
        --with-gapps) WITH_GAPPS=true; shift ;;
        --no-sync) DO_SYNC=false; shift ;;
        -h|--help) usage; exit 0 ;;
        -*) die "unknown option $1 (see --help)" ;;
        *) [ -z "$TREE_ARG" ] || die "only one <aosp-tree> allowed"; TREE_ARG="$1"; shift ;;
    esac
done
[ -n "$TREE_ARG" ] || { usage; exit 2; }
[[ "$DEPTH" =~ ^[0-9]+$ ]] || die "--depth must be a number"
[[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || die "--jobs must be a positive number"
[[ "$ATTEMPTS" =~ ^[1-9][0-9]*$ ]] || die "--attempts must be a positive number"
[[ "$RETRY_FETCHES" =~ ^[0-9]+$ ]] || die "--retry-fetches must be a number"

duo_require_cmds repo git git-lfs python3 flock
HARNESS="$(duo_harness_root)"
mkdir -p -- "$TREE_ARG"
TREE="$(duo_tree_path "$TREE_ARG")"
cd -- "$TREE"

# Same exclusive lock as dev/build.sh, held until exit.
mkdir -p .duo-dev
exec 9>.duo-dev/lock
flock -n 9 || die "a dev/build.sh or dev/sync.sh is running on $TREE; not touching it"

if [ ! -d .repo/manifests ]; then
    log "Initializing $TREE at $DUO_AOSP_BRANCH"
    init_args=(-u "$DUO_AOSP_MANIFEST_URL" -b "$DUO_AOSP_BRANCH" --git-lfs)
    if [ "$DEPTH" -gt 0 ]; then init_args+=("--depth=$DEPTH"); fi
    repo init "${init_args[@]}"
else
    merge="$(git config --file .repo/manifests.git/config --get branch.default.merge || true)"
    if [ -z "$merge" ]; then
        warn "cannot read the manifest branch of the existing tree; expected $DUO_AOSP_BRANCH"
    elif [[ "$merge" != *"$DUO_AOSP_BRANCH" ]]; then
        die "tree is initialized for '$merge', expected $DUO_AOSP_BRANCH; use another tree"
    fi
    log "Using existing repo client ($merge)"
fi

log "Installing local manifests"
mkdir -p .repo/local_manifests
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
for f in .repo/local_manifests/*; do
    [ -e "$f" ] || continue
    name="$(basename -- "$f")"
    managed=false
    for m in "${DUO_LOCAL_MANIFESTS[@]}"; do [ "$name" == "$m" ] && managed=true; done
    if ! $managed; then
        mkdir -p .repo/local_manifests.disabled
        log "  moving unmanaged $name to .repo/local_manifests.disabled/$name.$stamp"
        mv -- "$f" ".repo/local_manifests.disabled/$name.$stamp"
    fi
done
install -m 0644 "$HARNESS/build/dev/pinned.xml" .repo/local_manifests/duo-de-pinned.xml
install -m 0644 "$HARNESS/build/remove.xml" .repo/local_manifests/remove.xml
if $WITH_GAPPS; then
    install -m 0644 "$HARNESS/build/dev/pinned-gapps.xml" .repo/local_manifests/duo-de-gapps.xml
else
    rm -f .repo/local_manifests/duo-de-gapps.xml
fi

if ! $DO_SYNC; then
    log "Skipping repo sync (--no-sync)"
    exit 0
fi

# build.sh replaces this tracked prebuilt with the freshly built TrebleApp;
# restore it so `repo sync` does not stop on a modified file.
if [ -d vendor/hardware_overlay/.git ] || [ -f vendor/hardware_overlay/.git ]; then
    if ! git -C vendor/hardware_overlay diff --quiet -- TrebleApp/app.apk 2>/dev/null; then
        log "Restoring vendor/hardware_overlay/TrebleApp/app.apk before sync"
        git -C vendor/hardware_overlay checkout -- TrebleApp/app.apk
    fi
fi

# A tree synced before the LineageOS WebView change still has AOSP's
# platform/external/chromium-webview checked out at external/chromium-webview.
# The pinned manifest removes that project and adds LineageOS projects nested
# inside its path. repo checks the new ones out before deleting the obsolete
# checkout, which then looks dirty ("Cannot remove project: uncommitted changes
# are present"), so every sync attempt fails. Move the old checkout aside first,
# but only if it holds nothing besides those nested projects and links.
OLD_WEBVIEW=external/chromium-webview
move_old_webview_aside() {
    grep -qx "$OLD_WEBVIEW" .repo/project.list 2>/dev/null || return 0
    [ -e "$OLD_WEBVIEW/.git" ] || return 0
    grep -qs '<remove-project name="platform/external/chromium-webview"' .repo/local_manifests/*.xml || return 0
    git -C "$OLD_WEBVIEW" config --get-regexp '^remote\..*\.projectname$' 2>/dev/null \
        | grep -q ' platform/external/chromium-webview$' || return 0
    local status line unexpected=()
    status="$(git -C "$OLD_WEBVIEW" status --porcelain=v1 --untracked-files=normal)" \
        || die "cannot read the status of the obsolete AOSP $OLD_WEBVIEW checkout; move it out of the tree and rerun"
    while IFS= read -r line; do
        case "$line" in
            ''|'?? patches/'|'?? prebuilt/'|'?? prebuilt/arm64/'|'?? README'|'?? Android.bp') ;;
            *) unexpected+=("$line") ;;
        esac
    done <<<"$status"
    if [ ${#unexpected[@]} -gt 0 ]; then
        printf '  %s\n' "${unexpected[@]}" >&2
        die "the obsolete AOSP $OLD_WEBVIEW checkout has local changes (listed above), so repo cannot replace it with LineageOS's WebView; keep what you need, move $TREE/$OLD_WEBVIEW out of the tree and rerun"
    fi
    local dest=".duo-dev/obsolete/external-chromium-webview.$stamp"
    mkdir -p .duo-dev/obsolete
    : > .duo-dev/obsolete/.find-ignore   # Soong must not find the old Android.bp
    log "Moving the obsolete AOSP $OLD_WEBVIEW checkout to $TREE/$dest; delete it when no longer needed"
    mv -- "$OLD_WEBVIEW" "$dest"
}
move_old_webview_aside

SYNC_LOG=.duo-dev/repo-sync.log
sync_args=(-c --no-clone-bundle --no-tags "-j$JOBS" "--retry-fetches=$RETRY_FETCHES")
pause=60
for ((attempt = 1; ; attempt++)); do
    log "Syncing, attempt $attempt/$ATTEMPTS (repo sync ${sync_args[*]})"
    duo_repo sync "${sync_args[@]}" 2>&1 | tee "$SYNC_LOG" && break
    if grep -q 'Cannot remove project' "$SYNC_LOG"; then
        die "repo sync cannot remove an obsolete project that has local changes or untracked files (see above; retrying would not help). Move that directory out of the tree, or clean it, and rerun"
    fi
    if grep -qE '\b429\b|Too Many Requests|RESOURCE_EXHAUSTED' "$SYNC_LOG"; then
        reason="HTTP 429 rate limiting; on repeated failures wait and rerun with fewer --jobs"
    else
        reason="not an HTTP 429; see the errors above"
    fi
    [ "$attempt" -lt "$ATTEMPTS" ] || die "repo sync failed $ATTEMPTS times ($reason); full output of the last attempt: $TREE/$SYNC_LOG"
    warn "repo sync failed ($reason); retrying in ${pause}s"
    sleep "$pause"
    pause=$((pause * 2))
done

mkdir -p .duo-dev
duo_repo manifest -r -o .duo-dev/synced-manifest.xml
log "Synced. Resolved manifest: $TREE/.duo-dev/synced-manifest.xml"
