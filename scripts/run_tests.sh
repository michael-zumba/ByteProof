#!/usr/bin/env bash
# Run the ByteProof test suite one file per process.
#
# The suite builds real Qt windows, tray icons and floating panels. Qt's C++
# objects are freed by Python's garbage collector, so in a single process a
# window from one file can be collected while a later file is constructing its
# own - which intermittently segfaults on macOS. One process per file keeps
# every run deterministic and still exercises the whole suite.
#
# Usage: ./scripts/run_tests.sh [extra pytest args]
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$ROOT/venv/bin/python"
if [ ! -x "$PYTHON" ]; then
    PYTHON="$(command -v python3)"
fi

export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"

status=0
for file in "$ROOT"/tests/test_*.py; do
    name="$(basename "$file")"
    echo "=== $name"
    if ! "$PYTHON" -m pytest "$file" -q -p no:cacheprovider "$@"; then
        status=1
    fi
done

if [ "$status" -eq 0 ]; then
    echo "All test files passed."
else
    echo "Some test files failed." >&2
fi
exit "$status"
