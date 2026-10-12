"""Find source files and decide which language each one is written in."""

import os
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


def language_of(path: str | Path) -> str | None:
    file_path = Path(path)
    name = file_path.name
    if name.endswith(".d.ts"):
        return None
    return EXTENSIONS.get(file_path.suffix)


def is_test_file(path: str | Path) -> bool:
    file_path = Path(path)
    name = file_path.name
    if name.endswith("_test.go"):
        return True
    if name.endswith(("_test.clj", "_test.cljc", "_test.cljs", "_test.bb")):
        return True
    if name.endswith(
        (
            ".test.ts",
            ".spec.ts",
            ".test.tsx",
            ".spec.tsx",
            ".test.mts",
            ".spec.mts",
        )
    ):
        return True
    if name.endswith("_spec.lua"):
        return True
    if name == "conftest.py":
        return True
    if name.endswith("_test.py") or (name.startswith("test_") and name.endswith(".py")):
        return True
    return any(part in TEST_DIRS for part in file_path.parts)


def _walk(root: Path) -> list[Path]:
    if not root.exists():
        return []
    if root.is_file():
        return [root]
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name for name in dirnames if name not in SKIP_DIRS and name not in TEST_DIRS
        ]
        for name in filenames:
            path = Path(dirpath) / name
            if language_of(path) is None or is_test_file(path):
                continue
            found.append(path)
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


def exclude_files(files: list[Path], project_root: Path, exclusions: list[str]) -> list[Path]:
    """Apply project-relative exclusions after any source selection mode."""
    if not exclusions:
        return files
    patterns = [
        value.replace("\\", "/").removeprefix("./").rstrip("/")
        for value in exclusions
    ]
    kept = []
    for path in files:
        try:
            relative = path.resolve().relative_to(project_root.resolve()).as_posix()
        except ValueError:
            kept.append(path)
            continue
        if not any(_matches_exclusion(relative, pattern) for pattern in patterns):
            kept.append(path)
    return kept


def iter_source_files(roots: list[Path]) -> list[Path]:
    files: set[Path] = set()
    for root in roots:
        for path in _walk(root):
            files.add(path.resolve())
    return sorted(files, key=lambda path: path.as_posix())
