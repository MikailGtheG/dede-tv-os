#!/bin/sh
# Exercise the real resume wrapper without starting Kodi or touching a live DB.
set -eu
script=build/config/includes.chroot/usr/local/libexec/dede-tv-resume
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/media"
cat > "$tmp/bin/timeout" <<'EOF'
#!/bin/sh
# Skip timeout flags; execute the mocked core with the original arguments.
[ "$1" = "--signal=TERM" ] || exit 90
[ "$2" = "--kill-after=2s" ] || exit 91
shift 3
exec "$@"
EOF
cat > "$tmp/bin/core" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "$DEDE_TEST_LOG"
case "$DEDE_TEST_MODE" in
  fail) exit 1 ;;
  success) exit 0 ;;
  *) exit 92 ;;
esac
EOF
chmod +x "$tmp/bin/timeout" "$tmp/bin/core"
export PATH="$tmp/bin:$PATH" DEDE_CORE_BIN="$tmp/bin/core"
export DEDE_DB_PATH="$tmp/channels.db" DEDE_MEDIA_ROOT="$tmp/media"
export DEDE_RESUME_ATTEMPTS=2 DEDE_RESUME_DELAY=0 DEDE_RESUME_CALL_TIMEOUT=3
export DEDE_TEST_LOG="$tmp/calls" DEDE_TEST_MODE=success
# No database: never invoke the playback core.
sh "$script"
test ! -e "$tmp/calls"
: > "$DEDE_DB_PATH"
sh "$script"
test "$(wc -l < "$tmp/calls")" -eq 1
grep -F -- "--db $DEDE_DB_PATH resume-last $DEDE_MEDIA_ROOT" "$tmp/calls"
: > "$tmp/calls"
export DEDE_TEST_MODE=fail
if sh "$script"; then
  echo 'Exhausted resume attempts were incorrectly accepted' >&2
  exit 1
fi
test "$(wc -l < "$tmp/calls")" -eq 2
: > "$tmp/calls"
export DEDE_RESUME_ATTEMPTS=0 DEDE_TEST_MODE=success
if sh "$script"; then
  echo 'Invalid attempt count was accepted' >&2
  exit 1
fi
test ! -s "$tmp/calls"
echo 'Resume service regression checks passed'
