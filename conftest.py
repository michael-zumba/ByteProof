"""Shared pytest configuration.

Two jobs:

* Progress reporting for CI. With the default reporter a test that hangs prints
  nothing until it is already dead, so the log cannot say which test wedged the
  runner. ``BYTEPROOF_CI_PROGRESS=1`` makes every test announce itself before it
  runs, which is what turned an unexplained 20 minute stall into an exact test
  name and line number.
* Keeping the tests out of the user's real data folder. They exercise the live
  service, which writes its diagnostics to ``capture.log`` in the app support
  directory - the same file the owner reads when something goes wrong. Running
  the suite used to interleave thousands of synthetic lines into it.
"""

from __future__ import annotations

import os

import pytest

_PROGRESS = os.environ.get("BYTEPROOF_CI_PROGRESS") == "1"


@pytest.fixture(autouse=True)
def _isolated_support_dir(tmp_path, monkeypatch):
    """Point every support-directory lookup at a throwaway folder."""
    from src import generic_editing, settings

    # Keep the branded shape: some tests assert on the folder's components.
    sandbox = str(tmp_path / "ByteMind" / "ByteProof")
    os.makedirs(sandbox, exist_ok=True)
    monkeypatch.setattr(settings, "APP_SUPPORT_DIR", sandbox)
    monkeypatch.setattr(settings, "SETTINGS_FILE", os.path.join(sandbox, "settings.json"))
    # generic_editing imported the function by name, so patch the reference it
    # actually calls.
    monkeypatch.setattr(generic_editing, "get_app_support_dir", lambda: sandbox)
    yield sandbox


def pytest_runtest_logstart(nodeid: str, location: tuple[str, int | None, str]) -> None:
    if _PROGRESS:
        _, line, _ = location
        where = f"{nodeid} (line {line})" if line else nodeid
        print(f"--> {where}", flush=True)


def pytest_collection_finish(session) -> None:
    if _PROGRESS:
        print(f"--> collected {len(session.items)} tests", flush=True)
