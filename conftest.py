"""Shared pytest configuration.

The only job here is progress reporting for CI: with the default reporter a
test that hangs prints nothing until it is already dead, so the log cannot say
which test wedged the runner. ``BYTEPROOF_CI_PROGRESS=1`` makes every test
announce itself before it runs, which is what turned an unexplained 20 minute
stall into an exact test name and line number.
"""

from __future__ import annotations

import os

_PROGRESS = os.environ.get("BYTEPROOF_CI_PROGRESS") == "1"


def pytest_runtest_logstart(nodeid: str, location: tuple[str, int | None, str]) -> None:
    if _PROGRESS:
        _, line, _ = location
        where = f"{nodeid} (line {line})" if line else nodeid
        print(f"--> {where}", flush=True)


def pytest_collection_finish(session) -> None:
    if _PROGRESS:
        print(f"--> collected {len(session.items)} tests", flush=True)
