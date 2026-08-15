#!/usr/bin/env bash
#
# One-command ByteProof release from macOS.
#
# Usage:
#   ./scripts/release.sh 1.6.0 "Short release notes for users"
#
# This script:
#   1. Bumps the version in the app code, Windows metadata, and the website feed
#   2. Commits and pushes a vX.Y.Z tag -> GitHub Actions builds the Windows zip
#   3. Builds the Apple Silicon and Intel DMGs locally while Windows builds
#   4. Waits for the Windows build, then uploads both DMGs to the GitHub Release
#   5. Pushes the update feed to the ByteMind website (GitHub Pages deploys it)
#
# If anything fails, fix the problem and run the same command again. Existing
# tags are detected and skipped, so re-runs continue from where they stopped.

set -euo pipefail

REPO="michael-zumba/ByteProof"
WORKFLOW="build-release.yml"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEBSITE_DIR="$(dirname "$ROOT")/ByteMind_Website"

VERSION="${1:-}"
NOTES="${2:-}"
TAG="v$VERSION"

if [[ -z "$VERSION" ]]; then
    echo "Usage: $0 X.Y.Z \"release notes\"" >&2
    exit 1
fi

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Error: version must look like 1.6.0 (got '$VERSION')" >&2
    exit 1
fi

# --- Prerequisites -----------------------------------------------------------

if ! command -v gh >/dev/null 2>&1; then
    echo "Error: GitHub CLI not found. Install it with: brew install gh" >&2
    exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
    echo "Error: not logged in to GitHub. Run: gh auth login" >&2
    exit 1
fi

if ! command -v create-dmg >/dev/null 2>&1; then
    echo "Error: create-dmg not found. Install it with: brew install create-dmg" >&2
    exit 1
fi

if [[ ! -f "$ROOT/build_macos.sh" ]]; then
    echo "Error: missing build script: $ROOT/build_macos.sh" >&2
    exit 1
fi

if [[ ! -d "$WEBSITE_DIR" ]]; then
    echo "Error: website folder not found at $WEBSITE_DIR" >&2
    echo "Expected ByteMind_Website next to ByteProof in the ByteMind Project folder." >&2
    exit 1
fi

cd "$ROOT"

# Release artifacts must always match committed code, including when a release
# is resumed after a failed DMG build with the tag already pushed.
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "Error: ByteProof has uncommitted changes. Commit or stash them first:" >&2
    git status --porcelain --untracked-files=no >&2
    exit 1
fi

# --- Step 1-2: bump version, commit, push tag (skipped if tag already exists) -

TAG_EXISTS=0
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null 2>&1 ||
   git ls-remote --tags origin "$TAG" 2>/dev/null | grep -q "refs/tags/$TAG$"; then
    TAG_EXISTS=1
fi

if [[ "$TAG_EXISTS" -eq 0 ]]; then
    echo "=== Bumping ByteProof to $VERSION ==="
    python3 "$ROOT/tools/bump_version.py" "$VERSION" "$NOTES"

    echo "=== Committing and pushing tag $TAG ==="
    git add src/settings.py version_info.txt byteproof-version.json.example
    git commit -m "Release $VERSION: $NOTES"
    git tag -a "$TAG" -m "ByteProof $VERSION"
    git push origin main
    git push origin "$TAG"
    echo "Windows build started in GitHub Actions."
else
    echo "=== Tag $TAG already exists; skipping version bump and tag push ==="
fi

# --- Step 3: build both DMGs locally while GitHub builds Windows --------------

echo "=== Building Apple Silicon DMG ==="
"$ROOT/build_macos.sh" arm64

echo "=== Building Intel DMG ==="
"$ROOT/build_macos.sh" x86_64

# --- Step 4: wait for the Windows build, then upload DMGs --------------------

echo "=== Waiting for the GitHub Actions Windows build ==="
RUN_ID=""
for _ in {1..30}; do
    RUN_ID="$(gh run list --repo "$REPO" --workflow "$WORKFLOW" --event push --branch "$TAG" --limit 1 --json databaseId --jq '.[0].databaseId // empty' 2>/dev/null || true)"
    if [[ -n "$RUN_ID" ]]; then
        break
    fi
    sleep 10
done

if [[ -z "$RUN_ID" ]]; then
    echo "Error: could not find the GitHub Actions run for $TAG." >&2
    echo "Check https://github.com/$REPO/actions" >&2
    exit 1
fi

BUILD_OK=0
for attempt in 1 2; do
    if gh run watch "$RUN_ID" --repo "$REPO" --exit-status --interval 30; then
        BUILD_OK=1
        break
    fi
    if [[ "$attempt" -eq 2 ]]; then
        echo "Error: Windows build failed twice." >&2
        echo "Fix the failure, then re-run: gh run rerun $RUN_ID --repo $REPO --failed" >&2
        exit 1
    fi
    echo "Windows build failed; re-running once..."
    gh run rerun "$RUN_ID" --repo "$REPO" --failed >/dev/null 2>&1 || true
    sleep 15
done

echo "=== Waiting for the GitHub Release ==="
for _ in {1..60}; do
    if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
        break
    fi
    sleep 10
done

DMG_ARM="$ROOT/ByteProof_Installer_AppleSilicon.dmg"
DMG_INTEL="$ROOT/ByteProof_Installer_Intel.dmg"
for dmg in "$DMG_ARM" "$DMG_INTEL"; do
    if [[ ! -f "$dmg" ]]; then
        echo "Error: missing $dmg" >&2
        exit 1
    fi
done

echo "=== Uploading macOS DMGs to release $TAG ==="
gh release upload "$TAG" "$DMG_ARM" "$DMG_INTEL" --repo "$REPO" --clobber

# --- Step 5: publish the update feed to the website --------------------------

echo "=== Publishing ByteProof $VERSION to the website ==="
cd "$WEBSITE_DIR"
git add byteproof-version.json
if git diff --cached --quiet; then
    echo "Website feed already up to date."
else
    git commit -m "ByteProof $VERSION update feed"
    git push origin main
fi

echo "=== Verifying live update feed ==="
for _ in {1..30}; do
    LIVE="$(curl -fsSL --max-time 15 "https://www.bytemind.co.nz/byteproof-version.json" 2>/dev/null || true)"
    if [[ "$LIVE" == *"\"$VERSION\""* ]]; then
        echo "Done! The website now advertises ByteProof $VERSION."
        echo "Release: https://github.com/$REPO/releases/tag/$TAG"
        exit 0
    fi
    sleep 10
done

echo "Website push completed but the live feed is not updated yet." >&2
echo "Check https://www.bytemind.co.nz/byteproof-version.json shortly." >&2
exit 1
