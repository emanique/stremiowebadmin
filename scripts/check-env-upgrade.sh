#!/usr/bin/env sh
set -eu

LOCAL_ENV="${1:-.env}"
TEMPLATE_ENV="${2:-.env.example}"

if [ ! -f "$LOCAL_ENV" ]; then
  echo "[env-check] missing local file: $LOCAL_ENV" >&2
  exit 2
fi

if [ ! -f "$TEMPLATE_ENV" ]; then
  echo "[env-check] missing template file: $TEMPLATE_ENV" >&2
  exit 2
fi

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT HUP INT TERM

extract_names() {
  sed -n 's/^[[:space:]]*\([A-Z][A-Z0-9_]*\)[[:space:]]*=.*/\1/p' "$1" | sort -u
}

extract_names "$TEMPLATE_ENV" > "$tmpdir/template.vars"
extract_names "$LOCAL_ENV" > "$tmpdir/local.vars"

echo "[env-check] variables present in the new template but missing from local .env:"
if ! comm -23 "$tmpdir/template.vars" "$tmpdir/local.vars" | sed 's/^/  + /'; then
  exit 1
fi

missing="$(comm -23 "$tmpdir/template.vars" "$tmpdir/local.vars" | wc -l | tr -d ' ')"
obsolete="$(comm -13 "$tmpdir/template.vars" "$tmpdir/local.vars" | wc -l | tr -d ' ')"

if [ "$missing" -eq 0 ]; then
  echo "  none"
fi

echo "[env-check] local variables no longer present in the new template:"
if [ "$obsolete" -eq 0 ]; then
  echo "  none"
else
  comm -13 "$tmpdir/template.vars" "$tmpdir/local.vars" | sed 's/^/  - /'
fi

echo "[env-check] summary: missing=$missing obsolete=$obsolete"
echo "[env-check] no files were modified."

if [ "$missing" -gt 0 ]; then
  echo "[env-check] review the new variables before starting the upgraded stack." >&2
  exit 3
fi

exit 0
