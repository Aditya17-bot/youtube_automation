#!/usr/bin/env bash
# Run every test. Each is a plain script so failures surface with a traceback.
set -u
cd "$(dirname "$0")"
PY=.venv/Scripts/python.exe
fail=0
for t in tests/test_*.py; do
  printf '%-34s ' "$(basename "$t")"
  if out=$("$PY" "$t" 2>&1); then
    echo "PASS"
  else
    echo "FAIL"; echo "$out" | tail -6; fail=1
  fi
done
exit $fail
