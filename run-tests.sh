#!/usr/bin/env bash
# Every test, no pytest, no dependencies. One command, one exit code.
#   ./run-tests.sh            all tests
#   ./run-tests.sh -q         only failures + summary
#   ./run-tests.sh test_wheel run matching files only
set -u
quiet=0
filter=""
for a in "$@"; do
  case "$a" in
    -q) quiet=1 ;;
    *) filter="$a" ;;
  esac
done

fail=0
count=0
for t in tests/test_*.py; do
  name=$(basename "$t")
  [ -n "$filter" ] && case "$name" in *"$filter"*) ;; *) continue ;; esac
  count=$((count + 1))
  [ "$quiet" -eq 0 ] && printf '%-28s ' "$name"
  if timeout 120 python3 "$t" >/tmp/mahoraga-test.log 2>&1; then
    [ "$quiet" -eq 0 ] && echo PASS
  else
    echo "FAIL  $name"
    tail -15 /tmp/mahoraga-test.log
    fail=1
  fi
done

echo
if [ "$fail" -eq 0 ]; then
  echo "all green — $count file(s)"
else
  echo "FAILURES — see above"
fi
exit "$fail"
