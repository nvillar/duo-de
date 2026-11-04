#!/bin/bash
# Create or update an Android tree for DUO-DE developer builds, using the
# pinned manifest (build/dev/pinned.xml) and build/remove.xml.
#
# Usage: dev/sync.sh [options] <aosp-tree>
#
#   --depth N        clone depth for a new `repo init` (default 1; 0 = full history)
#   --jobs N         parallel sync jobs (default: nproc)
#   --with-gapps     also install build/dev/pinned-gapps.xml (vendor/gapps)
#   --no-sync        only install the local manifests, do not run `repo sync`
#
# Only <aosp-tree> is modified. Unknown files in .repo/local_manifests are moved
# to .repo/local_manifests.disabled/, not deleted. No `git clean`, no reset.
# Projects carrying DUO-DE patch commits are moved back to their manifest
# revision by `repo sync`; dev/build.sh re-applies the patches afterwards.

set -euo pipefail
# shellcheck source=dev/lib.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"

DEPTH=1
JOBS="$(duo_jobs_default)"
WITH_GAPPS=false
DO_SYNC=true
TREE_ARG=""

usage() { sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --depth) DEPTH="${2:?--depth needs a value}"; shift 2 ;;
        --jobs) JOBS="${2:?--jobs needs a value}"; shift 2 ;;
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

duo_require_cmds repo git git-lfs python3
HARNESS="$(duo_harness_root)"
mkdir -p -- "$TREE_ARG"
TREE="$(duo_tree_path "$TREE_ARG")"
cd -- "$TREE"

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

sync_args=(-c --no-clone-bundle --no-tags "-j$JOBS")
log "Syncing (repo sync ${sync_args[*]})"
if ! repo sync "${sync_args[@]}"; then
    warn "repo sync failed; retrying once"
    repo sync "${sync_args[@]}"
fi

mkdir -p .duo-dev
repo manifest -r -o .duo-dev/synced-manifest.xml
log "Synced. Resolved manifest: $TREE/.duo-dev/synced-manifest.xml"
