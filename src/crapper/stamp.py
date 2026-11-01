"""Remember which coverage reports match the tree, so a later tool can reuse them.

`target/coverage/crapper-stamp.json` holds one tree fingerprint per language
whose coverage succeeded, and the source files that run covered. Any edit,
addition, or removal outside the skipped directories and tool caches changes
the fingerprint. A run during which the tree changed stamps nothing, because
its reports may describe the old files.
"""

import hashlib
import json
import os
import sys
from pathlib import Path

from crapper.atomic import write_text
from crapper.discover import SKIP_DIRS, language_of

VERSION = 1
STAMP_NAME = "crapper-stamp.json"

# Written by the test and coverage tools themselves, never read by them as input.
CACHE_DIRS = {
    ".pytest_cache",
    ".hypothesis",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".cpcache",
    ".nyc_output",
    "htmlcov",
}


def stamp_path(root: Path) -> Path:
    return root / "target" / "coverage" / STAMP_NAME


def _cache_file(name: str) -> bool:
    return name == ".coverage" or name.startswith(".coverage.")


def _tree(root: Path):
    pruned = SKIP_DIRS | CACHE_DIRS
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in pruned]
        yield from (Path(dirpath) / name for name in filenames if not _cache_file(name))


def _row(path: Path, root: Path) -> list | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return [path.relative_to(root).as_posix(), info.st_size, info.st_mtime_ns]


def fingerprint(root: Path) -> str:
    """sha256 over (relative path, size, mtime_ns) of every file but caches, tests included."""

    rows = sorted(row for row in (_row(path, root) for path in _tree(root)) if row is not None)
    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps(row).encode("ascii") + b"\n")
    return digest.hexdigest()


def _valid(data) -> bool:
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return False
    languages, files = data.get("languages"), data.get("files")
    if not isinstance(languages, dict) or not isinstance(files, dict):
        return False
    return all(isinstance(value, str) for value in languages.values()) and all(
        isinstance(value, list) for value in files.values()
    )


def _read(root: Path) -> dict | None:
    try:
        data = json.loads(stamp_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if _valid(data) else None


def _relative(file: Path, root: Path) -> str:
    path = Path(file).resolve()
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()


def _covered(root: Path, files: list[Path]) -> dict[str, list[str]]:
    found: dict[str, set[str]] = {}
    for file in files:
        language = language_of(file)
        if language is not None:
            found.setdefault(language, set()).add(_relative(file, root))
    return {language: sorted(paths) for language, paths in found.items()}


def _publish(root: Path, data: dict) -> None:
    path = stamp_path(root)
    try:
        if data["languages"]:
            write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
        else:
            path.unlink(missing_ok=True)
    except OSError as exc:
        print(f"Could not record the coverage stamp: {exc}", file=sys.stderr)


def _base(root: Path, command: str | None) -> dict:
    data = _read(root)
    if data is None or data.get("command") != command:
        return {"version": VERSION, "command": command, "languages": {}, "files": {}}
    return data


def _settled(before: str, current: str) -> bool:
    if before != current:
        print("Files changed while coverage ran. Its reports are not stamped as fresh.", file=sys.stderr)
    return before == current


def record(root: Path, files: list[Path], command: str | None, failed: list[str], before: str) -> None:
    """Stamp the languages in `files` that did not fail. Drop the ones that did.

    `before` is the fingerprint taken when the run started. When the tree changed
    since then, every language in `files` is dropped.
    """

    data = _base(root, command)
    current = fingerprint(root)
    settled = _settled(before, current)
    for language, covered in _covered(root, files).items():
        if language in failed or not settled:
            data["languages"].pop(language, None)
            data["files"].pop(language, None)
        else:
            data["languages"][language] = current
            data["files"][language] = covered
    _publish(root, data)


def _fresh(data: dict, current: str, file: Path, root: Path) -> bool:
    language = language_of(file)
    return data["languages"].get(language) == current and _relative(file, root) in data["files"].get(language, [])


def stale_files(root: Path, files: list[Path], coverage_command: str | None) -> list[Path]:
    """The files whose coverage would have to be regenerated before it can be trusted."""

    root = root.resolve()
    data = _read(root)
    if data is None or data.get("command") != coverage_command:
        return list(files)
    current = fingerprint(root)
    return [file for file in files if not _fresh(data, current, file, root)]
