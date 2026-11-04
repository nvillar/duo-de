#!/bin/bash
# Run the DUO-DE developer scripts inside a rootless Podman container.
#
# Usage:
#   dev/podman.sh image                          build or refresh the build image
#   dev/podman.sh sync  <aosp-tree> [options]    run dev/sync.sh in the container
#   dev/podman.sh build <aosp-tree> [options]    run dev/build.sh in the container
#   dev/podman.sh shell <aosp-tree>              interactive shell in the container
#
# Environment:
#   DUO_DEV_IMAGE        image name (default localhost/duo-de-build:22.04)
#   DUO_DEV_CPUS         CPU limit (--cpus) and default build --jobs (default:
#                        no limit, --jobs = all host CPUs; sync keeps its own
#                        conservative default)
#   DUO_DEV_MEMORY       memory limit (--memory), e.g. 48g (default: none; WSL's
#                        .wslconfig limit applies)
#   DUO_DEV_PODMAN_ARGS  extra `podman run` arguments (word-split)
#   Resource limits need a cgroup v2 host with delegated controllers; on other
#   hosts Podman may ignore them with a warning or refuse to start.
#
# The tree is mounted read-write at the same path, the harness checkout
# read-only. Runs as your own user (--userns=keep-id). Works with Podman 3.4.
# Docker is not used.

set -euo pipefail
# shellcheck source=dev/lib.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"

IMAGE="${DUO_DEV_IMAGE:-localhost/duo-de-build:22.04}"
HARNESS="$(duo_harness_root)"
CONTAINERFILE="$HARNESS/dev/Containerfile"

usage() { sed -n '2,23p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

duo_require_cmds podman
cmd="${1:-}"
[ $# -gt 0 ] && shift

case "$cmd" in
    image)
        log "Building $IMAGE from dev/Containerfile"
        podman build --pull -t "$IMAGE" -f "$CONTAINERFILE" "$HARNESS/dev"
        podman image inspect --format 'Image id: {{.Id}}' "$IMAGE"
        exit 0
        ;;
    sync|build|shell) ;;
    -h|--help|"") usage; exit 0 ;;
    *) die "unknown command '$cmd' (see --help)" ;;
esac

[ $# -gt 0 ] || die "missing <aosp-tree>"
mkdir -p -- "$1"
TREE="$(duo_tree_path "$1")"
shift

podman image exists "$IMAGE" || die "image $IMAGE not found; run: dev/podman.sh image"
IMAGE_ID="$(podman image inspect --format '{{.Id}}' "$IMAGE")"

CPUS="${DUO_DEV_CPUS:-$(duo_jobs_default)}"
[[ "$CPUS" =~ ^[1-9][0-9]*$ ]] || die "DUO_DEV_CPUS must be a positive integer"

mkdir -p -- "$TREE/.duo-dev/home"
run_args=(
    --rm
    --userns=keep-id
    --security-opt label=disable
    --hostname duo-de-build
    --pids-limit=-1
    -v "$TREE:$TREE"
    -v "$HARNESS:$HARNESS:ro"
    -w "$TREE"
    -e HOME="$TREE/.duo-dev/home"
    -e USER="$(id -un)"
    -e DUO_DEV_CONTAINER_IMAGE="$IMAGE"
    -e DUO_DEV_CONTAINER_IMAGE_ID="$IMAGE_ID"
    -e DUO_DEV_CONTAINERFILE_SHA256="$(sha256sum "$CONTAINERFILE" | cut -d' ' -f1)"
)
[ -z "${DUO_DEV_CPUS:-}" ] || run_args+=(--cpus="$CPUS")
[ -z "${DUO_DEV_MEMORY:-}" ] || run_args+=(--memory="$DUO_DEV_MEMORY")
if [ -t 0 ] && [ -t 1 ]; then run_args+=(-it); fi

# A separate artifacts directory has to be mounted too.
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
    if [ "${args[$i]}" == --artifacts-dir ] && [ $((i + 1)) -lt ${#args[@]} ]; then
        mkdir -p -- "${args[$((i + 1))]}"
        art="$(cd -- "${args[$((i + 1))]}" && pwd -P)"
        args[i + 1]="$art"
        case "$art/" in "$TREE"/*) ;; *) run_args+=(-v "$art:$art") ;; esac
    fi
done

# shellcheck disable=SC2206
[ -z "${DUO_DEV_PODMAN_ARGS:-}" ] || run_args+=(${DUO_DEV_PODMAN_ARGS})

case "$cmd" in
    sync)  exec podman run "${run_args[@]}" "$IMAGE" bash "$HARNESS/dev/sync.sh" "${args[@]}" "$TREE" ;;
    build) exec podman run "${run_args[@]}" "$IMAGE" bash "$HARNESS/dev/build.sh" --jobs "$CPUS" "${args[@]}" "$TREE" ;;
    shell) exec podman run "${run_args[@]}" "$IMAGE" bash ;;
esac
