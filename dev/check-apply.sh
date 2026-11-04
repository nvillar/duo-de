#!/bin/bash
# Check that the whole DUO-DE patch series applies to a clean tree, reporting
# every failing patch instead of stopping at the first one. Use it after
# updating patches (for example after a TrebleDroid sync) or the AOSP tag.
#
# Usage: dev/check-apply.sh [options] <aosp-tree>
#
#   --unshallow   first fetch full history and android-16.0.0_r* base tags for
#                 the patched projects (as dev/build.sh --unshallow)
#   --restore     afterwards, move the patched projects back to their manifest
#                 revision (default: leave them as the check left them)
#
# DESTRUCTIVE for the patched projects: each is force-checked-out at its
# manifest revision (refs/remotes/m/*), discarding tracked changes and DUO-DE
# patch commits, exactly like dev/build.sh --reset-patches. Other projects are
# not touched. Takes the tree lock shared with dev/sync.sh and dev/build.sh.
# Afterwards dev/build.sh re-applies the patches itself.
#
# Patch sets are applied in upstream order (trebledroid, personal, duo), each
# patch with `git am -3`, in patch.sh's project and file order. After a failed
# patch the attempt is aborted and the rest of that project's patches (in all
# later sets too) are skipped, since they usually build on it.
# Exit status: 0 if everything applied, 1 if any patch failed.

set -euo pipefail
# shellcheck source=dev/lib.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"

UNSHALLOW=false
RESTORE=false
TREE_ARG=""
usage() { sed -n '2,23p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --unshallow) UNSHALLOW=true; shift ;;
        --restore) RESTORE=true; shift ;;
        -h|--help) usage; exit 0 ;;
        -*) die "unknown option $1 (see --help)" ;;
        *) [ -z "$TREE_ARG" ] || die "only one <aosp-tree> allowed"; TREE_ARG="$1"; shift ;;
    esac
done
[ -n "$TREE_ARG" ] || { usage; exit 2; }

duo_require_cmds git flock
HARNESS="$(duo_harness_root)"
TREE="$(duo_tree_path "$TREE_ARG")"
[ -d "$TREE/.repo" ] || die "$TREE is not a repo client (see dev/sync.sh)"
cd -- "$TREE"
mkdir -p .duo-dev
exec 9>.duo-dev/lock
flock -n 9 || die "a dev/build.sh, dev/sync.sh or dev/check-apply.sh is running on $TREE"

PATCH_ROOT=".duo-dev/check-patchroot"
declare -a PROJECT_PATHS=()
declare -A SEEN=()
prepare_patch_root
duo_prepare_history "$UNSHALLOW" "${PROJECT_PATHS[@]}"

log "Resetting ${#PROJECT_PATHS[@]} patched projects to their manifest revision"
for p in "${PROJECT_PATHS[@]}"; do
    if am_in_progress "$p"; then (cd -- "$p" && git am --abort) || true; fi
    git -C "$p" checkout -q -f --detach "$(proj_base "$p")"
done
# The recorded patch state no longer describes the tree.
rm -f .duo-dev/patch-state

export LC_ALL=C GIT_COMMITTER_NAME="DUO-DE check-apply" GIT_COMMITTER_EMAIL="duo-dev@localhost"
declare -A FAILED=()
declare -a FAILURES=()
total=0 applied=0 skipped=0
for set in "${DUO_PATCH_SETS[@]}"; do
    log "Checking $set patches"
    for dir in "$PATCH_ROOT/patches/$set"/*; do
        [ -e "$dir" ] || continue
        p="$(duo_project_path "$(basename -- "$dir")")"
        for patch in "$dir"/*.patch; do
            [ -e "$patch" ] || continue
            total=$((total + 1))
            name="$set/$(basename -- "$dir")/$(basename -- "$patch")"
            if [ -n "${FAILED[$p]:-}" ]; then
                skipped=$((skipped + 1))
                continue
            fi
            if out="$(git -C "$p" am -3 "$(readlink -f -- "$patch")" 2>&1)"; then
                applied=$((applied + 1))
            else
                (cd -- "$p" && git am --abort) >/dev/null 2>&1 || true
                FAILED[$p]="$name"
                reason="$(grep -m1 -E '^(error|fatal|CONFLICT)' <<<"$out" || echo "git am failed")"
                FAILURES+=("$name ($p): $reason")
                warn "FAILED $name: $reason"
            fi
        done
    done
done

if $RESTORE; then
    log "Restoring patched projects to their manifest revision"
    for p in "${PROJECT_PATHS[@]}"; do git -C "$p" checkout -q -f --detach "$(proj_base "$p")"; done
fi

echo
echo "Patches: $total, applied: $applied, failed: ${#FAILURES[@]}, skipped after a failure: $skipped"
if [ ${#FAILURES[@]} -gt 0 ]; then
    printf '  %s\n' "${FAILURES[@]}"
    echo "\"could not build fake ancestor\" usually means missing history: rerun with --unshallow."
    exit 1
fi
echo "All patches apply."