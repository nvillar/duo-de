#!/usr/bin/env bash
# Host tests and getevent replay for the three-finger gesture classifier (frameworks_base patch 0020).
#
# The classifier, DuoPanelGeometry and the JUnit tests are taken from the patch files in patches/duo/, so the tests
# exercise exactly what dev/build.sh applies. Nothing is written outside a temporary directory.
#
#   tools/duo-gesture/duo-gesture.sh test                       # unit tests + replay tests on testdata/
#   tools/duo-gesture/duo-gesture.sh replay [options] FILE...   # print the results for raw captures
#
# Java 17+ and JUnit 4 are taken from $JAVA_HOME / PATH and $JUNIT_CP, or else from the Android tree's prebuilts
# (read only): $AOSP/prebuilts/jdk/jdk21 and $AOSP/prebuilts/devtools/tools/lib/{junit-4.12,hamcrest-core-1.3}.jar.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../.." && pwd)
aosp=${AOSP:-$HOME/aosp}
fwb_tree=""

usage() {
    cat <<EOF
usage: $(basename "$0") [--aosp DIR] [--fwb DIR] test
       $(basename "$0") [--aosp DIR] [--fwb DIR] replay [--config k=v,...] [--rotation 0-3]
                                                  [--device /dev/input/eventN] [--all] CAPTURE...

  --aosp DIR  Android tree for the prebuilt JDK and JUnit (default: \$AOSP or ~/aosp)
  --fwb DIR   take the sources from this frameworks/base checkout instead of the patch files
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --aosp) aosp=$2; shift 2 ;;
        --fwb) fwb_tree=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) break ;;
    esac
done
[[ $# -ge 1 ]] || { usage >&2; exit 2; }
cmd=$1; shift

if [[ -n "${JAVA_HOME:-}" && -x "$JAVA_HOME/bin/javac" ]]; then
    jbin=$JAVA_HOME/bin
elif command -v javac >/dev/null 2>&1; then
    jbin=$(dirname "$(command -v javac)")
elif [[ -x "$aosp/prebuilts/jdk/jdk21/linux-x86/bin/javac" ]]; then
    jbin=$aosp/prebuilts/jdk/jdk21/linux-x86/bin
else
    echo "error: no javac (set JAVA_HOME, or --aosp to an Android tree with prebuilts/jdk/jdk21)" >&2
    exit 1
fi
junit_cp=${JUNIT_CP:-$aosp/prebuilts/devtools/tools/lib/junit-4.12.jar:$aosp/prebuilts/devtools/tools/lib/hamcrest-core-1.3.jar}

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
src=$work/src
mkdir -p "$src" "$work/classes"

geometry=core/java/com/android/internal/policy/DuoPanelGeometry.java
classifier=packages/SystemUI/src/com/android/systemui/duo/gesture/DuoThreeFingerClassifier.java
tests=packages/SystemUI/tests/DuoGestureTests/src/com/android/systemui/duo/gesture
files=("$geometry" "$classifier" "$tests/TouchScript.java" "$tests/DuoThreeFingerClassifierTest.java")

one_patch() {
    local matches=("$repo"/patches/duo/platform_frameworks_base/$1)
    [[ ${#matches[@]} -eq 1 && -f "${matches[0]}" ]] || {
        echo "error: expected one patch matching $1, found: ${matches[*]}" >&2
        exit 1
    }
    printf '%s\n' "${matches[0]}"
}

if [[ -n "$fwb_tree" ]]; then
    for f in "${files[@]}"; do
        mkdir -p "$src/$(dirname "$f")"
        cp "$fwb_tree/$f" "$src/$f"
    done
else
    # Both patches only add these files, so they apply to an empty directory.
    geometry_patch=$(one_patch '*-DuoPanelGeometry-*.patch')
    gesture_patch=$(one_patch '*-three-finger-gesture*.patch')
    (cd "$src" && git apply --include="$geometry" "$geometry_patch")
    (cd "$src" && git apply --include="$classifier" --include="$tests/TouchScript.java" \
        --include="$tests/DuoThreeFingerClassifierTest.java" "$gesture_patch")
    for f in "${files[@]}"; do
        [[ -f "$src/$f" ]] || { echo "error: $f not found in the patches" >&2; exit 1; }
    done
fi

sources=("${files[@]/#/$src/}")
sources+=("$here/src/org/duode/gesture/GeteventReplay.java")
if [[ "$cmd" == test ]]; then
    sources+=("$here/tests/org/duode/gesture/ReplayFixtureTest.java")
fi
"$jbin/javac" -nowarn -encoding UTF-8 -d "$work/classes" -cp "$junit_cp" "${sources[@]}"

case "$cmd" in
    test)
        "$jbin/java" -Dduo.gesture.testdata="$here/testdata" -cp "$work/classes:$junit_cp" \
            org.junit.runner.JUnitCore \
            com.android.systemui.duo.gesture.DuoThreeFingerClassifierTest \
            org.duode.gesture.ReplayFixtureTest
        ;;
    replay)
        "$jbin/java" -cp "$work/classes" org.duode.gesture.GeteventReplay "$@"
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac
