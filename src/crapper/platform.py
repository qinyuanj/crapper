"""What differs between the hosts crapper runs on: Linux and native Windows.

Every OS-specific decision lives here, selected once from `sys.platform`, so
analysis and report code stay platform-agnostic.
"""

from __future__ import annotations

import sys

WINDOWS = sys.platform == "win32"

# Forked workers inherit the loaded parsers instead of importing them again.
# Windows has no fork, so its workers start fresh and load parsers themselves.
POOL_START_METHOD = "spawn" if WINDOWS else "fork"

# Windows 10 22H2. Older builds are refused before any work.
MINIMUM_WINDOWS_BUILD = 19045


def windows_build() -> int | None:
    """The host's Windows build number, or None off Windows."""

    return sys.getwindowsversion().build if WINDOWS else None


def refusal(platform: str, build: int | None) -> str | None:
    """Why crapper will not run on `platform` at Windows `build`, or None when it will."""

    if platform != "win32" or build is None or build >= MINIMUM_WINDOWS_BUILD:
        return None
    return (
        f"crapper needs Windows 10 22H2 (build {MINIMUM_WINDOWS_BUILD}) or later; "
        f"this is build {build}. Update Windows, or run crapper inside WSL2."
    )
