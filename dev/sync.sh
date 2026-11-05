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
# If AOSP's obsolete external/chromium-webview checkout (replaced by LineageOS's
# WebView) is still there, it stops before syncing, reports local work in it and
# prints one-time manual steps; it never moves or deletes that checkout.
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

usage() { sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

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
# platform/external/chromium-webview checked out at external/chromium-webview,
# and the LineageOS projects that replace it are nested inside that path. repo
# checks the nested projects out before it deletes the obsolete checkout, which
# then looks dirty ("Cannot remove project"), so every sync fails. A safe move
# depends on the checkout's relative .git link into .repo/projects (where its
# index, stashes and branches live) and on the nested checkouts, which repo
# re-creates, resetting their index, if they disappear. So dev/sync.sh never
# moves or deletes anything here: it stops, reports local work and prints steps
# that move the old checkout to .duo-dev/obsolete/ with a repaired .git link and
# leave the nested checkouts at their paths.
OLD_WEBVIEW=external/chromium-webview
OLD_WEBVIEW_GITDIR=.repo/projects/external/chromium-webview.git
NESTED_WEBVIEW=(patches prebuilt/arm64)   # LineageOS projects, relative to $OLD_WEBVIEW

# One line per kind of local work in the Git checkout $1 that its owner may want
# to keep. Further arguments are `git status --porcelain` lines to ignore.
local_work() {
    local dir="$1" line e ok base head n list gd common self wt
    shift
    if ! git -C "$dir" rev-parse --git-dir >/dev/null 2>&1; then
        echo "not a readable Git checkout"
        return 0
    fi
    gd="$(realpath -- "$(git -C "$dir" rev-parse --absolute-git-dir)")"
    common="$(realpath -- "$(git -C "$dir" rev-parse --path-format=absolute --git-common-dir)")"
    self="$(realpath -- "$dir")"
    [ "$gd" == "$common" ] || echo "is a linked worktree of $common"
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        ok=false
        for e in "$@"; do [ "$line" != "$e" ] || ok=true; done
        $ok || echo "not committed: '$line' (git status --ignored)"
    done < <(git --no-optional-locks -C "$dir" status --porcelain=v1 --ignored --untracked-files=all)
    list="$(git -C "$dir" ls-files -v | grep -v '^H ' || true)"
    [ -z "$list" ] || echo "index flags (assume-unchanged, skip-worktree or unmerged) on $(grep -c . <<<"$list") files, e.g. '$(head -n1 <<<"$list")' (git ls-files -v)"
    head="$(proj_head "$dir" || true)"
    base="$( (proj_base "$dir") 2>/dev/null || true)"
    if [ -z "$base" ]; then
        echo "no single manifest revision (refs/remotes/m/*) to compare HEAD with"
    elif [ "$head" != "$base" ]; then
        n="$(git -C "$dir" rev-list --count "$base..$head" 2>/dev/null || echo '?')"
        echo "HEAD ${head:0:12} is not the manifest revision ${base:0:12} ($n commits not in it)"
    fi
    list="$(git -C "$dir" for-each-ref --format='%(refname:short)' refs/heads/)"
    [ -z "$list" ] || echo "local branches: $(tr '\n' ' ' <<<"$list")"
    n="$(git -C "$dir" stash list | grep -c . || true)"
    [ "$n" -eq 0 ] || echo "stash: $n entries"
    for wt in "$common"/worktrees/*/gitdir; do
        [ -f "$wt" ] || continue
        wt="$(dirname -- "$(cat -- "$wt")")"
        [ "$(realpath -m -- "$wt")" == "$self" ] || echo "another worktree of this repository: $wt"
    done
}

# The one-time migration runs as a script that dev/sync.sh writes but never
# runs. It holds the tree lock, creates $MIGRATION_MARKER before it parks
# anything, can be rerun after an interruption, and removes the marker last.
MIGRATION_SCRIPT=.duo-dev/webview-migration.sh
MIGRATION_MARKER=.duo-dev/webview-migration.incomplete
MIGRATION_PARK=.duo-dev/webview-nested

write_migration_script() { # dest rel
    {
        echo '#!/bin/bash'
        echo "# One-time DUO-DE migration of AOSP's obsolete $OLD_WEBVIEW checkout, written by"
        echo "# dev/sync.sh at $stamp. Moves the old checkout to $1 and repairs its .git link;"
        echo "# parks the nested LineageOS checkouts in $MIGRATION_PARK meanwhile and puts them back;"
        echo "# removes only repo's README and Android.bp links from it, restoring a tracked Android.bp."
        echo "# Deletes nothing. Holds the tree lock. If it stops, run it again: it resumes."
        echo 'set -euo pipefail'
        printf 'cd -- %q\n' "$TREE"
        printf 'DEST=%q\n' "$1"
        printf 'GITLINK=%q\n' "$2"
        printf 'OLD=%q PARK=%q MARKER=%q\n' "$OLD_WEBVIEW" "$MIGRATION_PARK" "$MIGRATION_MARKER"
        cat <<'EOF'
NESTED=(patches prebuilt/arm64)
fail() { echo "ERROR: $*" >&2; exit 1; }
mkdir -p .duo-dev
exec 9>.duo-dev/lock
flock -n 9 || fail "dev/sync.sh or dev/build.sh is running on this tree; run this script again when it has finished"
[ -e "$MARKER" ] || echo "$DEST" > "$MARKER"
mkdir -p .duo-dev/obsolete "$PARK/prebuilt"
touch .duo-dev/obsolete/.find-ignore
if [ -e "$OLD/.git" ] || [ -L "$OLD/.git" ]; then
    for p in "${NESTED[@]}"; do
        if [ -e "$OLD/$p/.git" ] || [ -L "$OLD/$p/.git" ]; then
            [ ! -e "$PARK/$p" ] || fail "both $OLD/$p and $PARK/$p exist; resolve by hand"
            mv -- "$OLD/$p" "$PARK/$p"
        fi
    done
    [ ! -e "$DEST" ] || fail "$DEST already exists; resolve by hand"
    mv -- "$OLD" "$DEST"
fi
[ -d "$DEST" ] || fail "neither $OLD/.git nor $DEST exists; resolve by hand"
ln -sfn -- "$GITLINK" "$DEST/.git"
# Only repo's two linkfiles, with their exact targets. A tracked file they
# replaced is restored; the pending restoration is recorded before unlinking,
# so an interrupted run finishes it.
for f in README Android.bp; do
    case "$f" in README) t=patches/README ;; *) t=patches/os_pickup.bp ;; esac
    pending="$MARKER.restore-$f"
    if [ -L "$DEST/$f" ] && [ "$(readlink -- "$DEST/$f")" == "$t" ]; then
        if git -C "$DEST" ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then touch -- "$pending"; fi
        rm -- "$DEST/$f"
    fi
    if [ -e "$pending" ]; then
        git -C "$DEST" checkout -- "$f"
        rm -f -- "$pending"
    fi
done
for p in "${NESTED[@]}"; do
    if [ -e "$PARK/$p" ] || [ -L "$PARK/$p" ]; then
        [ ! -e "$OLD/$p" ] || fail "both $OLD/$p and $PARK/$p exist; resolve by hand"
        mkdir -p -- "$(dirname -- "$OLD/$p")"
        mv -- "$PARK/$p" "$OLD/$p"
    fi
done
rmdir -- "$PARK/prebuilt" "$PARK"
rm -f -- "$MARKER"
echo "WebView migration complete: old checkout in $DEST. Now rerun dev/sync.sh."
EOF
    } > "$MIGRATION_SCRIPT.tmp"
    mv -- "$MIGRATION_SCRIPT.tmp" "$MIGRATION_SCRIPT"
}

# An unfinished migration: the nested checkouts may be parked, and repo would
# re-create them (resetting their index) whatever the state of the old checkout.
check_webview_migration() {
    [ -e "$MIGRATION_MARKER" ] || [ -e "$MIGRATION_PARK" ] || return 0
    {
        echo "An earlier WebView migration has not finished:"
        [ ! -e "$MIGRATION_MARKER" ] || echo "  - $MIGRATION_MARKER exists"
        find "$MIGRATION_PARK" -mindepth 1 -maxdepth 2 ! -path "$MIGRATION_PARK/prebuilt" 2>/dev/null | sed 's/^/  - parked: /'
        if [ -f "$MIGRATION_SCRIPT" ]; then
            echo "Finish it with (it resumes where it stopped and holds the tree lock):"
            printf '    bash %q\n' "$TREE/$MIGRATION_SCRIPT"
        else
            echo "$MIGRATION_SCRIPT is missing: move each parked checkout back to the same path under $OLD_WEBVIEW,"
            echo "remove the empty $MIGRATION_PARK and $MIGRATION_MARKER, and rerun dev/sync.sh."
        fi
    } >&2
    die "stopped before repo sync: repo would re-create the parked LineageOS checkouts and reset their index"
}

check_old_webview() {
    [ -e "$OLD_WEBVIEW/.git" ] || [ -L "$OLD_WEBVIEW/.git" ] || return 0
    grep -qs '<remove-project name="platform/external/chromium-webview"' .repo/local_manifests/*.xml || return 0
    local p f t work desc dest rel nested=() expected=()
    for p in "${NESTED_WEBVIEW[@]}"; do
        if [ -e "$OLD_WEBVIEW/$p/.git" ] || [ -L "$OLD_WEBVIEW/$p/.git" ]; then
            nested+=("$p"); expected+=("?? $p/")
        fi
    done
    # repo's linkfiles from the patches project (build/dev/pinned.xml).
    for f in README Android.bp; do
        case "$f" in README) t=patches/README ;; *) t=patches/os_pickup.bp ;; esac
        if [ -L "$OLD_WEBVIEW/$f" ] && [ "$(readlink -- "$OLD_WEBVIEW/$f")" == "$t" ]; then
            expected+=("?? $f" " T $f")
        fi
    done
    {
        echo "$OLD_WEBVIEW is still AOSP's obsolete platform/external/chromium-webview checkout."
        echo "The DUO-DE manifest replaces it with LineageOS's WebView, whose projects are nested in that path;"
        echo "repo cannot remove the old checkout around them. dev/sync.sh never moves or deletes it."
        echo
        work="$(local_work "$OLD_WEBVIEW" "${expected[@]}")"
        if [ -n "$work" ]; then
            echo "Local work in $OLD_WEBVIEW (the migration keeps it, in the moved checkout):"
            head -n 40 <<<"$work" | sed 's/^/  - /'
        else
            echo "No local work found in $OLD_WEBVIEW (status incl. ignored files, index flags, HEAD, branches, stash, worktrees)."
        fi
        for p in "${nested[@]}"; do
            work="$(local_work "$OLD_WEBVIEW/$p")"
            [ -z "$work" ] && continue
            echo "Local work in the LineageOS checkout $OLD_WEBVIEW/$p (the migration puts it back in place):"
            head -n 40 <<<"$work" | sed 's/^/  - /'
        done
        echo
        if [ -L "$OLD_WEBVIEW/.git" ] && [ -n "$(realpath -e -- "$OLD_WEBVIEW_GITDIR" 2>/dev/null)" ] \
            && [ "$(realpath -e -- "$OLD_WEBVIEW/.git" 2>/dev/null)" == "$(realpath -e -- "$OLD_WEBVIEW_GITDIR")" ]; then
            dest=".duo-dev/obsolete/external-chromium-webview.$stamp"
            rel="$(realpath -m --relative-to="$TREE/$dest" -- "$TREE/$OLD_WEBVIEW_GITDIR")"
            write_migration_script "$dest" "$rel"
            echo "dev/sync.sh wrote the one-time migration to $MIGRATION_SCRIPT and does not run it. It moves"
            echo "nothing but the old checkout (to $dest, hidden from Soong by .find-ignore), repairs its relative"
            echo ".git link, keeps the LineageOS checkouts at their paths (parked meanwhile) and deletes nothing."
            echo "While it is unfinished, $MIGRATION_MARKER makes dev/sync.sh refuse to sync. Review it, then run:"
            printf '    bash %q\n' "$TREE/$MIGRATION_SCRIPT"
            echo "==== $MIGRATION_SCRIPT ===="
            cat -- "$MIGRATION_SCRIPT"
            echo "==== end of $MIGRATION_SCRIPT ===="
            echo "Then rerun dev/sync.sh. The moved checkout stays usable while $OLD_WEBVIEW_GITDIR and"
            echo ".repo/project-objects/platform/external/chromium-webview.git exist; delete it before those."
        else
            if [ -L "$OLD_WEBVIEW/.git" ]; then desc="a symlink to $(readlink -- "$OLD_WEBVIEW/.git")"
            elif [ -f "$OLD_WEBVIEW/.git" ]; then desc="a file: $(head -n1 -- "$OLD_WEBVIEW/.git")"
            else desc="a directory"; fi
            echo "$OLD_WEBVIEW/.git is not repo's link to $OLD_WEBVIEW_GITDIR but $desc, so dev/sync.sh has no"
            echo "migration for it. Move $OLD_WEBVIEW out of the tree yourself (a linked worktree with 'git worktree move'),"
            echo "keeping the LineageOS checkouts ${nested[*]:-(none yet)} at their paths under $OLD_WEBVIEW, then rerun dev/sync.sh."
        fi
    } >&2
    die "stopped before repo sync because of the obsolete $OLD_WEBVIEW checkout (see above and docs/development-setup.md, 5.1)"
}
check_webview_migration
check_old_webview

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
