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
  flaky)
    if [ ! -e "$DEDE_TEST_FLAKY_MARKER" ]; then
      : > "$DEDE_TEST_FLAKY_MARKER"
      exit 1
    fi
    exit 0 ;;
  success) exit 0 ;;
  hang) exec sleep 10 ;;
  remove-db) rm -f "$DEDE_DB_PATH"; exit 1 ;;
  replace-db) rm -f "$DEDE_DB_PATH"; printf "replacement\\n" > "$DEDE_DB_PATH"; exit 1 ;;
  redirect-db) rm -f "$DEDE_DB_PATH"; ln -s "$DEDE_TEST_REDIRECT_TARGET" "$DEDE_DB_PATH"; exit 1 ;;
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
# On first boot, no channel database is normal even if timeout is unavailable.
# Do not fail the resume service before discovering that there is nothing to play.
mkdir -p "$tmp/no-timeout"
PATH="$tmp/no-timeout" /bin/sh "$script" > "$tmp/first-boot-output" 2>&1
grep -q "No saved TV channel database" "$tmp/first-boot-output"
test ! -e "$tmp/calls"
# A symlinked database must not redirect automatic playback to another file.
printf 'private data\n' > "$tmp/redirect-target"
ln -s "$tmp/redirect-target" "$DEDE_DB_PATH"
if sh "$script"; then
  echo 'Symlinked channel database was accepted' >&2
  exit 1
fi
test ! -e "$tmp/calls"
grep -qx 'private data' "$tmp/redirect-target"
rm "$DEDE_DB_PATH"
# A dangling symlink must also be refused rather than treated as an absent
# database (which would incorrectly report a successful first boot).
ln -s "$tmp/missing-channel-db" "$DEDE_DB_PATH"
if sh "$script" > "$tmp/dangling-output" 2>&1; then
  echo 'Dangling channel database symlink was accepted' >&2
  exit 1
fi
grep -q 'Symlinked TV channel database refused' "$tmp/dangling-output"
test ! -e "$tmp/calls"
rm "$DEDE_DB_PATH"
: > "$DEDE_DB_PATH"
sh "$script"
test "$(wc -l < "$tmp/calls")" -eq 1
grep -F -- "--db $DEDE_DB_PATH resume-last $DEDE_MEDIA_ROOT" "$tmp/calls"
# Kodi may not yet accept JSON-RPC immediately after its process starts.
# A transient failure must be retried and then accepted without extra calls.
: > "$tmp/calls"
export DEDE_TEST_MODE=flaky DEDE_TEST_FLAKY_MARKER="$tmp/flaky-marker"
sh "$script"
test "$(wc -l < "$tmp/calls")" -eq 2
test -f "$DEDE_TEST_FLAKY_MARKER"
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
# Restore valid settings after the deliberately invalid-attempts case above.
# Otherwise that earlier validation masks the missing-timeout diagnostic.
export DEDE_RESUME_ATTEMPTS=2
# A missing timeout executable must fail immediately instead of retrying an
# unbounded core call or silently reporting a successful TV resume.
: > "$tmp/calls"
mkdir -p "$tmp/no-timeout"
if PATH="$tmp/no-timeout" /bin/sh "$script" > "$tmp/no-timeout-output" 2>&1; then
  echo 'Resume succeeded without the required timeout executable' >&2
  exit 1
fi
test ! -s "$tmp/calls"
grep -q 'GNU timeout is required' "$tmp/no-timeout-output"
# Exercise the real GNU timeout, not the argument-checking mock above.
# A hung playback request must terminate within the configured deadline.
: > "$tmp/calls"
export DEDE_TEST_MODE=hang DEDE_RESUME_ATTEMPTS=1 DEDE_RESUME_CALL_TIMEOUT=1
export PATH=/usr/bin:/bin
start=$(date +%s)
if sh "$script"; then
  echo 'Hung playback request was incorrectly accepted' >&2
  exit 1
fi
elapsed=$(( $(date +%s) - start ))
if [ "$elapsed" -gt 5 ]; then
  echo "Hung playback request exceeded timeout budget: ${elapsed}s" >&2
  exit 1
fi
test "$(wc -l < "$tmp/calls")" -eq 1
# A database removed during administration must stop retries immediately.
: > "$tmp/calls"
: > "$DEDE_DB_PATH"
export PATH="$tmp/bin:/usr/bin:/bin"
export DEDE_TEST_MODE=remove-db DEDE_RESUME_ATTEMPTS=3 DEDE_RESUME_CALL_TIMEOUT=3
if sh "$script"; then
  echo 'Resume accepted a database removed during playback' >&2
  exit 1
fi
test "$(wc -l < "$tmp/calls")" -eq 1
test ! -e "$DEDE_DB_PATH"
# A failed core call must not follow a database redirected by another process.
# The wrapper must stop after one attempt, without retrying the redirected path.
: > "$tmp/calls"
printf '%s\n' 'sentinel' > "$tmp/redirect-after-call"
: > "$DEDE_DB_PATH"
export DEDE_TEST_MODE=redirect-db DEDE_TEST_REDIRECT_TARGET="$tmp/redirect-after-call"
if sh "$script"; then
  echo 'Resume accepted a database redirected during playback' >&2
  exit 1
fi
test "$(wc -l < "$tmp/calls")" -eq 1
test -L "$DEDE_DB_PATH"
grep -qx sentinel "$DEDE_TEST_REDIRECT_TARGET"
# A different regular file at the same path must not be retried either.
: > "$tmp/calls"
: > "$DEDE_DB_PATH"
export DEDE_TEST_MODE=replace-db DEDE_RESUME_ATTEMPTS=3
if sh "$script"; then
  echo 'Resume accepted a replaced regular channel database' >&2
  exit 1
fi
test "$(wc -l < "$tmp/calls")" -eq 1
grep -qx replacement "$DEDE_DB_PATH"
echo 'Resume service regression checks passed'
