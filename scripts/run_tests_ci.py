"""Run each pytest file with a hard wall-clock limit.

CI must never wedge. A test that blocks inside a C call (a modal Qt dialog
nobody can dismiss, a COM or shell call, a stuck socket) ignores pytest's own
timeout plugin, and the runner's step timeout does not always land either --
the job then sits for the full job timeout, or longer, and the release waits
on a run that produces no output at all.

This wrapper starts each file in its own process group, streams its output
straight to the CI log, and kills the whole process tree when the limit is
passed. The log keeps everything the child printed, including the faulthandler
traceback that names the stuck frame, and the job finishes seconds later.

Usage:
    python scripts/run_tests_ci.py tests/test_smoke.py tests/test_hardening.py

Environment:
    BYTEPROOF_TEST_LIMIT_S  per-file wall-clock limit in seconds (default 180)
    BYTEPROOF_PYTEST_ARGS   extra pytest arguments, whitespace separated
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

DEFAULT_LIMIT_S = 180.0
DEFAULT_PYTEST_ARGS = (
    # faulthandler dumps every thread's traceback after 25s of no progress, so
    # the log names the stuck frame even when the timeout cannot interrupt it;
    # pytest-timeout then fails that test so the rest of the file still runs.
    "--timeout=45",
    "-o",
    "faulthandler_timeout=25",
    "--tb=short",
)


def _limit_seconds() -> float:
    try:
        return float(os.environ.get("BYTEPROOF_TEST_LIMIT_S", "") or DEFAULT_LIMIT_S)
    except ValueError:
        return DEFAULT_LIMIT_S


def _pytest_args() -> list[str]:
    extra = os.environ.get("BYTEPROOF_PYTEST_ARGS", "")
    return extra.split() if extra else list(DEFAULT_PYTEST_ARGS)


def _kill_tree(process: subprocess.Popen) -> None:
    """Kill the child and anything it started, on either platform."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.kill()
    except Exception:
        pass


def run_file(path: str, limit_s: float) -> bool:
    """Run one test file; return True when it passed inside the limit."""
    print(f"=== {path}", flush=True)
    started = time.monotonic()
    process = subprocess.Popen(
        [sys.executable, "-m", "pytest", path, "-v", "-p", "no:cacheprovider", *_pytest_args()],
        start_new_session=True,
    )
    try:
        returncode = process.wait(timeout=limit_s)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        elapsed = time.monotonic() - started
        print(
            f"!!! {path} made no progress for {elapsed:.0f}s "
            f"(limit {limit_s:.0f}s) and was killed",
            flush=True,
        )
        return False
    print(f"--- {path} finished in {time.monotonic() - started:.1f}s", flush=True)
    return returncode == 0


def main(argv: list[str]) -> int:
    paths = argv or ["tests"]
    limit_s = _limit_seconds()
    failed: list[str] = []
    for path in paths:
        if not run_file(path, limit_s):
            failed.append(path)
    if failed:
        print("failed: " + ", ".join(failed), flush=True)
        return 1
    print("All test files passed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
