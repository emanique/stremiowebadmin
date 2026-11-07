#!/usr/bin/env bash
set -euo pipefail

UPSTREAM_URL="${UPSTREAM_URL:-https://github.com/andrewhack/stremio-libtorrent-server.git}"
UPSTREAM_NAME="${UPSTREAM_NAME:-andrewhack/stremio-libtorrent-server}"
BASE_BRANCH="${BASE_BRANCH:-development}"
TARGET_BRANCH="${TARGET_BRANCH:-upstream/integration}"

emit_output() {
    if [ -n "${GITHUB_OUTPUT:-}" ]; then
        printf '%s=%s\n' "$1" "$2" >> "$GITHUB_OUTPUT"
    fi
}

git config user.name "${GIT_AUTHOR_NAME:-github-actions[bot]}"
git config user.email "${GIT_AUTHOR_EMAIL:-41898282+github-actions[bot]@users.noreply.github.com}"

git remote add upstream "$UPSTREAM_URL" 2>/dev/null || git remote set-url upstream "$UPSTREAM_URL"
git fetch --prune origin "$BASE_BRANCH"
git fetch --prune upstream main

# Seed the remote-tracking ref when a previous integration branch exists so
# --force-with-lease can verify that nobody changed it unexpectedly.
git fetch origin "$TARGET_BRANCH:refs/remotes/origin/$TARGET_BRANCH" 2>/dev/null || true

MAIN_SHA="$(git rev-parse "origin/$BASE_BRANCH")"
TARGET_SHA="$(git rev-parse upstream/main)"
TARGET_VERSION="$(git show "$TARGET_SHA:pyproject.toml" | sed -n 's/^version = "\(.*\)"/\1/p' | head -1)"
SHORT="${TARGET_SHA:0:12}"
REPORT="upstream-sync-report.md"

is_protected() {
    local candidate="$1" protected
    while IFS= read -r protected; do
        [ -n "$protected" ] || continue
        case "$protected" in \#*) continue ;; esac
        if [ "$candidate" = "$protected" ] || [ "${candidate#"$protected"/}" != "$candidate" ]; then
            return 0
        fi
    done < .github/upstream-protected-paths.txt
    return 1
}

restore_from_main() {
    local path="$1" entry mode blob

    if git cat-file -e "$MAIN_SHA:$path" 2>/dev/null; then
        entry="$(git ls-tree "$MAIN_SHA" -- "$path")"
        mode="$(printf '%s\n' "$entry" | awk '{print $1}')"
        blob="$(printf '%s\n' "$entry" | awk '{print $3}')"

        test -n "$mode"
        test -n "$blob"

        # Keep the exact blob from fork main. This avoids CRLF/LF normalization
        # creating a false modification in protected files.
        git update-index --add --cacheinfo "$mode,$blob,$path"

        mkdir -p "$(dirname "$path")"
        if [ "$mode" = "120000" ]; then
            rm -f -- "$path"
            ln -s "$(git cat-file blob "$blob")" "$path"
        else
            git cat-file blob "$blob" > "$path"
            if [ "$mode" = "100755" ]; then
                chmod +x "$path"
            fi
        fi
    else
        git rm -rf --ignore-unmatch -- "$path" >/dev/null 2>&1 || true
    fi
}

write_header() {
    {
        echo "# Controlled upstream sync"
        echo
        echo "Principal: $UPSTREAM_NAME"
        echo "Principal version: $TARGET_VERSION"
        echo "Principal SHA: $TARGET_SHA"
        echo "Fork $BASE_BRANCH before sync: $MAIN_SHA"
    } > "$REPORT"
}

if git merge-base --is-ancestor "$TARGET_SHA" "$MAIN_SHA"; then
    write_header
    {
        echo
        echo "Status: already up to date."
    } >> "$REPORT"

    emit_output changed false
    emit_output main_sha "$MAIN_SHA"
    emit_output target "$TARGET_SHA"
    emit_output version "$TARGET_VERSION"
    emit_output branch "$TARGET_BRANCH"

    cat "$REPORT"
    exit 0
fi

git checkout -B "$TARGET_BRANCH" "$MAIN_SHA"

set +e
git merge --no-ff --no-commit upstream/main
merge_rc=$?
set -e

if [ "$merge_rc" -ne 0 ]; then
    mapfile -t conflicts < <(git diff --name-only --diff-filter=U)

    for path in "${conflicts[@]}"; do
        if is_protected "$path"; then
            echo "Resolving protected conflict in favor of fork: $path"
            restore_from_main "$path"
        fi
    done

    mapfile -t remaining < <(git diff --name-only --diff-filter=U)
    if [ "${#remaining[@]}" -gt 0 ]; then
        write_header
        {
            echo
            echo "Status: blocked by manual conflicts."
            echo
            echo "Manual conflicts:"
            for path in "${remaining[@]}"; do
                echo "- $path"
            done
            echo
            echo "Protected conflicts were resolved automatically in favor of the fork."
            echo "No integration branch was pushed and main was not changed."
        } >> "$REPORT"

        emit_output changed false
        emit_output blocked true
        emit_output main_sha "$MAIN_SHA"
        emit_output target "$TARGET_SHA"
        emit_output version "$TARGET_VERSION"
        emit_output branch "$TARGET_BRANCH"

        git merge --abort || true
        cat "$REPORT"
        exit 2
    fi
fi

# Restore every protected path touched by upstream, including paths that merged
# without a textual conflict.
mapfile -t changed_paths < <(git diff --name-only "$MAIN_SHA")
protected_paths=()

for path in "${changed_paths[@]}"; do
    if is_protected "$path"; then
        protected_paths+=("$path")
        echo "Restoring fork-owned path exactly: $path"
        restore_from_main "$path"
    fi
done

# This protected marker intentionally advances only after the merge itself is
# clean. The merge is still not promoted to main until CI passes.
printf '%s\n' "$TARGET_SHA" > .github/UPSTREAM_BASE
git add .github/UPSTREAM_BASE

git commit -m "Merge upstream $TARGET_VERSION ($SHORT) with fork protections"

INTEGRATION_SHA="$(git rev-parse HEAD)"
git push --force-with-lease origin "$TARGET_BRANCH"

write_header
{
    echo "Integration SHA: $INTEGRATION_SHA"
    echo "Integration branch: $TARGET_BRANCH"
    echo
    echo "Status: merge prepared; CI validation required before promotion."
    echo
    echo "Protected fork paths restored:"
    if [ "${#protected_paths[@]}" -eq 0 ]; then
        echo "- none"
    else
        for path in "${protected_paths[@]}"; do
            echo "- $path"
        done
    fi
} >> "$REPORT"

emit_output changed true
emit_output blocked false
emit_output main_sha "$MAIN_SHA"
emit_output target "$TARGET_SHA"
emit_output version "$TARGET_VERSION"
emit_output branch "$TARGET_BRANCH"
emit_output integration_sha "$INTEGRATION_SHA"

cat "$REPORT"
