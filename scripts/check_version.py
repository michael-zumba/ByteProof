#!/usr/bin/env python3
"""Verify that every version marker in the repository agrees.

ByteProof tracks its version in three places:

* ``src/settings.py`` - ``APP_VERSION``, compiled into the running app
* ``version_info.txt`` - Windows file metadata (and the MSIX package version)
* the git tag, when releasing (``v2.0.2``)

A mismatch means testers cannot tell which build they have, or an installer is
published under the wrong number. Run in CI, and locally before a release:

    python scripts/check_version.py
    python scripts/check_version.py --tag v2.0.2
"""

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([A-Za-z]+)\.?(\d+)?)?$")


def read_app_version() -> str:
    path = os.path.join(ROOT, "src", "settings.py")
    with open(path, encoding="utf-8") as handle:
        match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', handle.read())
    if not match:
        raise SystemExit("error: APP_VERSION not found in src/settings.py")
    return match.group(1)


def read_file_version() -> str:
    path = os.path.join(ROOT, "version_info.txt")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    match = re.search(r"StringStruct\(u'FileVersion',\s*u'([^']+)'\)", text)
    if not match:
        raise SystemExit("error: FileVersion not found in version_info.txt")
    return match.group(1)


def read_tuple_version() -> tuple[int, int, int, int]:
    path = os.path.join(ROOT, "version_info.txt")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    match = re.search(r"filevers=\(([^)]*)\)", text)
    if not match:
        raise SystemExit("error: filevers not found in version_info.txt")
    parts = [int(part.strip()) for part in match.group(1).split(",")]
    while len(parts) < 4:
        parts.append(0)
    return (parts[0], parts[1], parts[2], parts[3])


def expected_tuple(version: str) -> tuple[int, int, int, int]:
    match = VERSION_RE.match(version)
    if not match:
        raise SystemExit(f"error: {version!r} is not a valid version string")
    major, minor, patch, _stage, number = match.groups()
    build = int(number) if number else 0
    return (int(major), int(minor), int(patch), build)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="", help="git tag to compare, e.g. v2.0.2")
    args = parser.parse_args()

    app_version = read_app_version()
    file_version = read_file_version()
    tuple_version = read_tuple_version()

    problems: list[str] = []
    if file_version != app_version:
        problems.append(
            f"version_info.txt FileVersion {file_version!r} != "
            f"src/settings.py APP_VERSION {app_version!r}"
        )
    if tuple_version != expected_tuple(app_version):
        problems.append(
            f"version_info.txt filevers {tuple_version} != "
            f"{expected_tuple(app_version)} for {app_version!r}"
        )

    if args.tag:
        tag_version = args.tag.lstrip("vV")
        if tag_version != app_version:
            problems.append(
                f"git tag {args.tag!r} != APP_VERSION {app_version!r}"
            )

    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1

    print(f"version markers agree: {app_version} (tuple {tuple_version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
