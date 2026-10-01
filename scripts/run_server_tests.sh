#!/usr/bin/env bash
# Run the licence-service tests in their own virtualenv.
#
#   ./scripts/run_server_tests.sh            # all server tests
#   ./scripts/run_server_tests.sh -k portal  # a subset
#
# The venv (`.venv_server`) is created on first use and is gitignored.
set -euo pipefail

cd "$(dirname "$0")/.."

VENV=".venv_server"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet \
    -r server/requirements.txt -r server/requirements-dev.txt
fi

exec "$VENV/bin/python" -m pytest --confcutdir=server server/tests "$@"
