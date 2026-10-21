"""Turn source files and coverage into CRAP entries."""

import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from crapper.coverage import CoverageBundle
from crapper.crap import make_entry, sort_entries
from crapper.discover import language_of
from crapper.languages import functions_in_file
from crapper.model import Entry, Function

# Below this many files a process pool costs more than it saves.
PARALLEL_MIN_FILES = 64


def _parallel_min_files() -> int:
    return int(os.environ.get("CRAPPER_PARALLEL_MIN_FILES") or PARALLEL_MIN_FILES)


def _functions(file: Path, root: Path) -> list[Function]:
    language = language_of(file)
    if language is None:
        return []
    source = file.read_text(encoding="utf-8", errors="replace")
    try:
        relative = file.relative_to(root).as_posix()
    except ValueError:
        relative = file.as_posix()
    return functions_in_file(language, source, relative, str(root))


def _functions_by_file(files: list[Path], root: Path) -> list[list[Function]]:
    workers = os.cpu_count() or 1
    if len(files) < _parallel_min_files() or workers < 2:
        return [_functions(file, root) for file in files]
    # Forked workers inherit the loaded parsers instead of importing them again.
    with ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("fork")) as pool:
        chunk = max(1, len(files) // (workers * 4))
        return list(pool.map(_functions, files, [root] * len(files), chunksize=chunk))


def analyze_files(
    files: list[Path],
    project_root: Path,
    bundle: CoverageBundle | None,
) -> list[Entry]:
    root = project_root.resolve()
    entries: list[Entry] = []
    for functions in _functions_by_file([file.resolve() for file in files], root):
        for function in functions:
            coverage = None if bundle is None else bundle.percent_for(function)
            entries.append(make_entry(function, coverage))
    return sort_entries(entries)
