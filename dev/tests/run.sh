#!/bin/bash
# Run the developer-script mock tests in a temporary directory. No Android tree,
# network, container or device is used. Needs bash, git, python3, flock.
# Usage: dev/tests/run.sh      (exit status 1 if any check fails)
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DUO_TEST_DIR="$(mktemp -d)"
export DUO_TEST_DIR
trap 'rm -rf -- "$DUO_TEST_DIR"' EXIT
out="$DUO_TEST_DIR.log"
{ bash "$here/mock-patches.sh" "$(dirname -- "$(dirname -- "$here")")"; bash "$here/mock-build.sh"; } >"$out" 2>&1 || true
grep -E '^(PASS|FAIL)' "$out" || true
pass=$(grep -c '^PASS' "$out" || true)
if grep -q '^FAIL' "$out" || [ "$pass" -lt 56 ]; then
    echo "mock tests FAILED ($pass passed); log: $out"; exit 1
fi
rm -f -- "$out"
echo "mock tests passed ($pass checks)"