"""Find source files and decide which language each one is written in."""

import os
import subprocess
import sys
from fnmatch import fnmatchcase
from functools import cache
from pathlib import Path

EXTENSIONS = {
    ".clj": "clojure",
    ".cljc": "clojure",
    ".cljs": "clojure",
    ".bb": "clojure",
    ".java": "java",
    ".go": "go",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".rs": "rust",
    ".py": "python",
    ".lua": "lua",
}

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    "target",
    "dist",
    "build",
    "out",
    "coverage",
    ".metrics",
    ".uml-viewer",
    "testdata",
    "__pycache__",
    ".clj-kondo",
    "lua_modules",
    ".luarocks",
}

TEST_DIRS = {"test", "tests", "spec", "specs", "__tests__"}

_TEST_SUFFIXES = (
    "_test.go",
    "_test.clj",
    "_test.cljc",
    "_test.cljs",
    "_test.bb",
    ".test.ts",
    ".spec.ts",
    ".test.tsx",
    ".spec.tsx",
    ".test.mts",
    ".spec.mts",
    "_spec.lua",
    "_test.py",
)


def language_of(path: str | Path) -> str | None:
    file_path = Path(path)
    name = file_path.name
    if name.endswith(".d.ts"):
        return None
    return EXTENSIONS.get(file_path.suffix)


def is_test_file(path: str | Path) -> bool:
    file_path = Path(path)
    name = file_path.name
    if name.endswith(_TEST_SUFFIXES) or name == "conftest.py":
        return True
    if name.startswith("test_") and name.endswith(".py"):
        return True
    return any(part in TEST_DIRS for part in file_path.parts)


def is_source_file(path: Path) -> bool:
    return language_of(path) is not None and not is_test_file(path)


def _walk(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS and name not in TEST_DIRS]
        paths = (Path(dirpath) / name for name in filenames)
        found.extend(path for path in paths if is_source_file(path))
    return found


def _matches_exclusion(relative: str, pattern: str) -> bool:
    parts = tuple(relative.split("/"))
    segments = tuple(pattern.split("/"))

    @cache
    def matches(path_index: int, pattern_index: int) -> bool:
        if pattern_index == len(segments):
            return True  # A matching directory also excludes its descendants.
        segment = segments[pattern_index]
        if segment == "**":
            return matches(path_index, pattern_index + 1) or (
                path_index < len(parts) and matches(path_index + 1, pattern_index)
            )
        return (
            path_index < len(parts)
            and fnmatchcase(parts[path_index], segment)
            and matches(path_index + 1, pattern_index + 1)
        )

    return matches(0, 0)


def _pattern(value: str) -> str:
    return value.replace("\\", "/").removeprefix("./").rstrip("/")


def excluded(path: Path, root: Path, exclusions: list[str]) -> bool:
    """A match on a directory excludes everything under it. Outside `root` never matches."""

    try:
        relative = Path(path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return False
    return any(_matches_exclusion(relative, _pattern(value)) for value in exclusions)


def exclude_files(files: list[Path], project_root: Path, exclusions: list[str]) -> list[Path]:
    """Apply project-relative exclusions after any source selection mode."""

    if not exclusions:
        return files
    return [path for path in files if not excluded(path, project_root, exclusions)]


def without_excluded(files: list[Path], root: Path, patterns: list[str]) -> list[Path]:
    """The name dryer and mutator import; same rule as exclude_files."""

    return exclude_files(files, root, patterns)


def iter_source_files(roots: list[Path]) -> list[Path]:
    files: set[Path] = set()
    for root in roots:
        for path in _walk(root):
            files.add(path.resolve())
    return sorted(files, key=lambda path: path.as_posix())


def _git(root: Path, *args: str) -> bytes | None:
    result = subprocess.run(["git", *args], cwd=root, check=False, capture_output=True)
    if result.returncode != 0:
        print(os.fsdecode(result.stderr).strip() or "git status failed", file=sys.stderr)
        return None
    return result.stdout


def _status_paths(output: bytes) -> list[str]:
    """`git status -z` entries are `XY path`. Renames are off, so one path each.

    The empty entry after the last NUL names the repository itself, which the
    caller drops along with every other path that is not a file.
    """

    return [entry[3:] for entry in os.fsdecode(output).split("\0")]


_STATUS = ("status", "--porcelain", "-z", "--no-renames", "--untracked-files=all")


def _walked(path: Path, root: Path) -> bool:
    """A file under `root` that is outside every directory the walk skips."""

    if not path.is_file() or not path.is_relative_to(root):
        return False
    return SKIP_DIRS.isdisjoint(path.relative_to(root).parts[:-1])


def changed_files(root: Path) -> list[Path]:
    """Existing files under `root` that git reports as added, edited, or untracked.

    Files in the directories the walk skips (`vendor`, `node_modules`, ...) are left out.
    """

    root = root.resolve()
    top = _git(root, "rev-parse", "--show-toplevel")
    status = None if top is None else _git(root, *_STATUS)
    if status is None:
        return []
    base = Path(os.fsdecode(top).removesuffix("\n"))
    found = {(base / name).resolve() for name in _status_paths(status)}
    return sorted((path for path in found if _walked(path, root)), key=lambda path: path.as_posix())


def exclusion_error(pattern: str) -> str | None:
    """Exclusions are anchored at the project root and stay inside it."""

    normalized = pattern.replace("\\", "/")
    if Path(pattern).is_absolute() or normalized.startswith("/") or ".." in normalized.split("/"):
        return f"--exclude must be a path or glob inside the project root: {pattern}"
    return None


def under_roots(files: list[Path], roots: list[Path]) -> list[Path]:
    """Files inside at least one root, compared after resolving both sides. A file root holds itself."""

    resolved = [Path(root).resolve() for root in roots]
    return [path for path in files if any(Path(path).resolve().is_relative_to(root) for root in resolved)]
