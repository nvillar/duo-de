#!/bin/bash
# Builds backveto.dex (BackVeto.java) for app_process, against the framework classes of a built AOSP tree.
# Usage: build.sh [AOSP_DIR] [OUT_DIR]   (defaults: ~/aosp, this directory)
# Uses only prebuilts and an already built framework.jar; does not build or modify the tree.
set -euo pipefail
AOSP=${1:-$HOME/aosp}
OUT=${2:-$(cd "$(dirname "$0")" && pwd)}
SRC=$(cd "$(dirname "$0")" && pwd)/BackVeto.java
FW=$AOSP/out/soong/.intermediates/frameworks/base/framework-minus-apex/android_common/combined/framework.jar
[ -f "$FW" ] || { echo "missing $FW (build the tree first)" >&2; exit 1; }
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
cp "$FW" "$TMP/framework.jar"   # a copy: a running build may replace the original
JDK=$AOSP/prebuilts/jdk/jdk21/linux-x86
"$JDK/bin/javac" --release 11 -proc:none -nowarn -cp "$TMP/framework.jar" -d "$TMP/classes" "$SRC"
(cd "$TMP/classes" && "$JDK/bin/jar" cf "$TMP/backveto.jar" .)
"$JDK/bin/java" -cp "$AOSP/prebuilts/r8/r8.jar" com.android.tools.r8.D8 --min-api 34 \
    --lib "$JDK" --lib "$TMP/framework.jar" --output "$TMP" "$TMP/backveto.jar"
mv "$TMP/classes.dex" "$OUT/backveto.dex"
echo "$OUT/backveto.dex"
