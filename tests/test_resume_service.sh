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
  success-swap) mv "$DEDE_DB_PATH" "$DEDE_DB_PATH.before-success"; printf '%s\n' replacement > "$DEDE_DB_PATH"; exit 0 ;;
  success-redirect) rm -f "$DEDE_DB_PATH"; ln -s "$DEDE_TEST_REDIRECT_TARGET" "$DEDE_DB_PATH"; exit 0 ;;
  hang) exec sleep 10 ;;
  remove-db) rm -f "$DEDE_DB_PATH"; exit 1 ;;
  replace-db) mv "$DEDE_DB_PATH" "$DEDE_DB_PATH.old"; printf "replacement\\n" > "$DEDE_DB_PATH"; exit 1 ;;
  redirect-db) rm -f "$DEDE_DB_PATH"; ln -s "$DEDE_TEST_REDIRECT_TARGET" "$DEDE_DB_PATH"; exit 1 ;;
  *) exit 92 ;;
esac
EOF
chmod +x "$tmp/bin/timeout" "$tmp/bin/core"
export PATH="$tmp/bin:$PATH" DEDE_CORE_BIN="$tmp/bin/core"
export DEDE_DB_PATH="$tmp/channels.db" DEDE_MEDIA_ROOT="$tmp/media"
export DEDE_RESUME_ATTEMPTS=2 DEDE_RESUME_DELAY=0 DEDE_RESUME_CALL_TIMEOUT=3
export DEDE_TEST_LOG="$tmp/calls" DEDE_TEST_MODE=success
# A missing playback engine must fail visibly rather than report a successful
# boot-resume: otherwise the appliance could silently start without TV.
if DEDE_CORE_BIN="$tmp/missing-core" sh "$script" > "$tmp/missing-core-output" 2>&1; then
  echo 'Missing TV playback core was incorrectly accepted' >&2
  exit 1
fi
grep -q 'Dede TV core missing or not executable' "$tmp/missing-core-output"
test ! -e "$tmp/calls"
# An existing but non-executable core is equally unusable. Fail before any
# attempt, rather than treating a present file as a successful TV startup.
printf '%s\n' '#!/bin/sh' 'exit 0' > "$tmp/non-executable-core"
chmod 0644 "$tmp/non-executable-core"
if DEDE_CORE_BIN="$tmp/non-executable-core" sh "$script" > "$tmp/non-executable-output" 2>&1; then
  echo 'Non-executable TV playback core was incorrectly accepted' >&2
  exit 1
fi
grep -q 'Dede TV core missing or not executable' "$tmp/non-executable-output"
test ! -e "$tmp/calls"
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
# Restore a valid attempt count so the following checks exercise their own guards.
export DEDE_RESUME_ATTEMPTS=2
# Reject malformed retry delay and call timeout before invoking the core.
# These settings bound startup latency and must never be silently accepted.
for invalid_setting in delay timeout; do
  : > "$tmp/calls"
  case "$invalid_setting" in
    delay)
      export DEDE_RESUME_DELAY=11
      ;;
    timeout)
      export DEDE_RESUME_CALL_TIMEOUT=0
      ;;
  esac
  if sh "$script" > "$tmp/invalid-$invalid_setting-output" 2>&1; then
    echo "Invalid resume $invalid_setting was accepted" >&2
    exit 1
  fi
  test ! -s "$tmp/calls"
  case "$invalid_setting" in
    delay) unset DEDE_RESUME_DELAY ;;
    timeout) unset DEDE_RESUME_CALL_TIMEOUT ;;
  esac
done
# Non-numeric settings must fail closed without invoking the playback core.
# Include each field independently so a prior invalid value cannot mask a bug.
for invalid_setting in attempts delay timeout; do
  : > "$tmp/calls"
  case "$invalid_setting" in
    attempts) export DEDE_RESUME_ATTEMPTS='2x' ;;
    delay) export DEDE_RESUME_DELAY='1x' ;;
    timeout) export DEDE_RESUME_CALL_TIMEOUT='3x' ;;
  esac
  if sh "$script" > "$tmp/non-numeric-$invalid_setting-output" 2>&1; then
    echo "Non-numeric resume $invalid_setting was accepted" >&2
    exit 1
  fi
  test ! -s "$tmp/calls"
  case "$invalid_setting" in
    attempts) export DEDE_RESUME_ATTEMPTS=2 ;;
    delay) export DEDE_RESUME_DELAY=0 ;;
    timeout) export DEDE_RESUME_CALL_TIMEOUT=3 ;;
  esac
done
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
# The preceding redirect-db case leaves a symlink behind. Remove it first:
# truncating through it would modify the sentinel and make this test invalid.
rm -f -- "$DEDE_DB_PATH"
: > "$tmp/calls"
: > "$DEDE_DB_PATH"
export DEDE_TEST_MODE=replace-db DEDE_RESUME_ATTEMPTS=3
if sh "$script"; then
  echo 'Resume accepted a replaced regular channel database' >&2
  exit 1
fi
test "$(wc -l < "$tmp/calls")" -eq 1
grep -qx replacement "$DEDE_DB_PATH"
# A database swap during the retry delay must be caught before the next call.
# Mock sleep performs the swap while preserving the old inode via rename.
cat > "$tmp/bin/sleep" <<'EOF'
#!/bin/sh
if [ "${DEDE_TEST_SWAP_ON_DELAY:-0}" = 1 ]; then
  mv "$DEDE_DB_PATH" "$DEDE_DB_PATH.before-delay"
  printf '%s\n' 'new channel list' > "$DEDE_DB_PATH"
fi
exit 0
EOF
chmod +x "$tmp/bin/sleep"
: > "$tmp/calls"
: > "$DEDE_DB_PATH"
export PATH="$tmp/bin:/usr/bin:/bin" DEDE_TEST_MODE=fail
export DEDE_TEST_SWAP_ON_DELAY=1 DEDE_RESUME_ATTEMPTS=3 DEDE_RESUME_DELAY=1
if sh "$script"; then
  echo 'Resume accepted a database swapped during retry delay' >&2
  exit 1
fi
test "$(wc -l < "$tmp/calls")" -eq 1
grep -qx 'new channel list' "$DEDE_DB_PATH"
unset DEDE_TEST_SWAP_ON_DELAY
# A core that returns success after replacing the DB must not be accepted.
: > "$tmp/calls"
: > "$DEDE_DB_PATH"
export DEDE_TEST_MODE=success-swap DEDE_RESUME_ATTEMPTS=2 DEDE_RESUME_DELAY=0
if sh "$script"; then
  echo 'Resume accepted a database swapped during successful playback' >&2
  exit 1
fi
test "$(wc -l < "$tmp/calls")" -eq 1
grep -qx replacement "$DEDE_DB_PATH"
# A successful core call must not hide a symlink redirect either. Preserve the
# target file and verify that the wrapper rejects the redirected database.
: > "$tmp/calls"
rm -f -- "$DEDE_DB_PATH"
: > "$DEDE_DB_PATH"
printf '%s\n' 'do not modify' > "$tmp/success-redirect-target"
export DEDE_TEST_MODE=success-redirect DEDE_TEST_REDIRECT_TARGET="$tmp/success-redirect-target"
if sh "$script"; then
  echo 'Resume accepted a symlinked database after successful playback' >&2
  exit 1
fi
test "$(wc -l < "$tmp/calls")" -eq 1
test -L "$DEDE_DB_PATH"
grep -qx 'do not modify' "$DEDE_TEST_REDIRECT_TARGET"
echo 'Resume service regression checks passed'
