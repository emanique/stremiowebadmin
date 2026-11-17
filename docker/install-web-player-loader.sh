#!/bin/sh
# Install docker/web-player-loader.js over the loader the web build ships (the file says why).
# Usage: install-web-player-loader.sh <web-build-dir> <web-player-loader.js>
#
# The build keeps its scripts under a 40-hex commit directory, and its service worker precaches
# loader.js under a content revision. Both are updated: with the old revision left in place, a browser
# that has the service worker keeps running the old loader out of its cache. The build fails, rather
# than shipping the old loader, unless each is found exactly once.
set -eu

BUILD="$1"
LOADER="$2"
SW="$BUILD/service-worker.js"

set -- "$BUILD"/*/scripts/loader.js
if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
    echo "install-web-player-loader: expected one */scripts/loader.js under $BUILD, found: $*" >&2
    exit 1
fi
TARGET="$1"
REL="${TARGET#"$BUILD"/}"   # <commit>/scripts/loader.js, the path the service worker precaches

ENTRY="{url:\"$REL\",revision:\"[0-9a-f]*\"}"
FOUND=$(grep -o "$ENTRY" "$SW" | wc -l)
if [ "$FOUND" -ne 1 ]; then
    echo "install-web-player-loader: expected one precache entry for $REL in $SW, found $FOUND" >&2
    exit 1
fi

cp "$LOADER" "$TARGET"
# QA-on-demand WebPlayer integration. Keep generic loader fixtures unchanged;
# only the production image contains the additional hook files.
AI_CODE="$(dirname "$LOADER")/ai-demand-web-player.js"
AI_PATCH="$(dirname "$LOADER")/install-ai-demand-hook.py"
if [ "${STREMIOSRV_AI_PLAYER_PATCH:-0}" = "1" ]; then
    if [ ! -f "$AI_CODE" ] || [ ! -f "$AI_PATCH" ]; then
        echo "AI WebPlayer hook files missing" >&2
        exit 1
    fi
    python3 "$AI_PATCH" "$BUILD"
    cat "$AI_CODE" >> "$TARGET"
    MAIN_REL="${REL%/loader.js}/main.js"
    MAIN="$BUILD/$MAIN_REL"
    MAIN_ENTRY="{url:\"$MAIN_REL\",revision:\"[0-9a-f]*\"}"
    MAIN_FOUND=$(grep -o "$MAIN_ENTRY" "$SW" | wc -l)
    if [ "$MAIN_FOUND" -ne 1 ]; then
        echo "install-web-player-loader: main.js precache entry missing" >&2
        exit 1
    fi
    MAIN_REV=$(md5sum "$MAIN" | cut -d' ' -f1)
    sed -i "s|$MAIN_ENTRY|{url:\"$MAIN_REL\",revision:\"$MAIN_REV\"}|" "$SW"
fi
rm -f "$TARGET.map"         # described the replaced bundle; nothing points at it any more
REV=$(md5sum "$TARGET" | cut -d' ' -f1)
sed -i "s|$ENTRY|{url:\"$REL\",revision:\"$REV\"}|" "$SW"
if ! grep -q "{url:\"$REL\",revision:\"$REV\"}" "$SW"; then
    echo "install-web-player-loader: the new revision did not land in $SW" >&2
    exit 1
fi
echo "install-web-player-loader: $REL replaced, service worker revision $REV"
