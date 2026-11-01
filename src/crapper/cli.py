"""Command line for the multi-language CRAP tool."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from crapper.analyze import analyze_files
from crapper.coverage import load_bundle
from crapper.discover import (
    changed_files,
    exclude_files,
    excluded,
    exclusion_error,
    is_source_file,
    iter_source_files,
    language_of,
    under_roots,
)
from crapper.metrics import metrics_path, write_metrics
from crapper.report import format_report
from crapper.runners import CoverageError, run_coverage

HELP = """\
Usage: crapper [options] [path-or-filter ...]

Detect the language of each source file and score it with the CRAP metric
CRAP = CC² × (1 − coverage)³ + CC. Writes .metrics/crap.edn for uml-viewer
and prints a report sorted worst first.

Languages: Clojure (.clj .cljc .cljs .bb), Java (.java), Go (.go),
TypeScript (.ts .tsx .mts .cts), Rust (.rs), Python (.py), Lua (.lua).

Options:
  -h, --help                    Print this help and exit.
  --root <path>                 Project root. Metrics are written here.
                                Default: the current directory.
  -s, --source-root <path>      Walk this tree instead of the project root.
                                May be repeated. Path arguments and --changed
                                are narrowed to the files under a source root.
  --exclude <path-or-glob>      Exclude a project-relative file or directory.
                                May be repeated; applies to every selection mode.
                                Both separators work. * and ? match within a path
                                segment; ** matches zero or more whole segments.
                                Matching directories exclude their descendants.
                                An absolute pattern or one with .. is an error.
  --full-tree                   Replace the complete configured business tree.
                                Rejects --changed, positionals and existing coverage.
                                Any language's coverage failure keeps the previous
                                snapshot and exits 1. --no-coverage publishes
                                complexity only.
  --changed                     Analyze source files git reports as added,
                                modified, renamed, or untracked under the
                                project root, outside the skipped directories.
  --no-coverage                 Score complexity only. Coverage and CRAP are N/A.
  --use-existing-coverage       Read coverage already on disk. Do not rerun tests.
  --coverage-command <cmd>      Run this command instead of the per-language
                                coverage tools, then read the reports it wrote.
  --threshold <number>          Exit 2 when the worst CRAP score is above this.
  --lua-shards <n>              Split Lua spec files across n Busted coverage
                                shards. Default: the CPU count. 1 keeps one
                                process for specs that share state.

Arguments:
  path              File or directory to analyze. Test paths are included when
                    you name them explicitly. With --source-root, a named file
                    outside every root is dropped:
                    "Skipping <path>; it is outside the source roots" on stderr.
  filter            When the argument is not a path, only source files whose
                    path contains this text are analyzed.

With no paths, source files under the project root are analyzed. Directories
named test, tests, spec, specs, vendor, node_modules, and target are skipped,
as are *_test.go, *.spec.ts, test_*.py, *_test.py, and *_spec.lua files.

Exit status: 0 when the analysis completed, 2 when --threshold was exceeded
after the snapshot was written, 1 for a usage error, failed Lua or custom
coverage, failed --full-tree coverage, or a snapshot that could not be written.

Coverage, when it is produced:
  Clojure      clj -M:cov --lcov, then Cloverage form counts or LCOV
  Java         Maven JaCoCo instruction coverage, for each pom.xml module
  Go           go test ./... -coverprofile=...
  TypeScript   npm run coverage, Vitest's own LCOV, or c8 around npm test
  Rust         cargo llvm-cov, or cargo tarpaulin, in the nearest Cargo.toml
  Python       coverage.py LCOV, via pytest or unittest, per project
  Lua          busted -c with luacov, then luacov -r lcov, per .busted or rockspec
Languages whose coverage succeeded are stamped in
target/coverage/crapper-stamp.json, so mutator can reuse the reports until
any file outside the skipped directories and tool caches (.pytest_cache,
.cpcache, .coverage, ...) changes. A file edited while coverage runs leaves
that run unstamped. A coverage script is judged by its exit status; the other
coverage commands fail when they write no report.

uml-viewer joins .metrics/crap.edn on :namespace and :name.
  Clojure      the ns, and the defn name
  Java         package.Class, and the method name
  Go           import path, or import.path.Receiver for methods
  TypeScript   dotted module path, or module.Class for methods
  Rust         crate::module, or crate::module::Type for methods
  Python       dotted module path, or module.Class for methods
  Lua          require path, and the name as written (M.foo, Class:method)
"""


@dataclass
class Options:
    action: str
    message: str = ""
    exit_code: int = 0
    project_root: Path = field(default_factory=lambda: Path("."))
    source_roots: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)
    positionals: list[str] = field(default_factory=list)
    no_coverage: bool = False
    use_existing_coverage: bool = False
    coverage_command: str | None = None
    threshold: float | None = None
    changed: bool = False
    full_tree: bool = False
    lua_shards: int | None = None


def _take(args: list[str], index: int, option: str) -> str:
    if index + 1 >= len(args) or not args[index + 1] or args[index + 1].startswith("-"):
        raise ValueError(f"{option} requires a value")
    return args[index + 1]


def _threshold(raw: str) -> float:
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError("--threshold requires a number") from exc


def _lua_shards(raw: str) -> int:
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError("--lua-shards requires a positive integer")
    return int(raw)


_VALUES = {
    "--root": ("project_root", Path),
    "--coverage-command": ("coverage_command", str),
    "--threshold": ("threshold", _threshold),
    "--lua-shards": ("lua_shards", _lua_shards),
}
_LISTS = {"-s": "source_roots", "--source-root": "source_roots", "--exclude": "exclusions"}
_FLAGS = {
    "--no-coverage": "no_coverage",
    "--use-existing-coverage": "use_existing_coverage",
    "--changed": "changed",
    "--full-tree": "full_tree",
}


def _apply(options: Options, args: list[str], index: int) -> int:
    """Apply the argument at `index`. Returns the index of the next one."""

    arg = args[index]
    if arg in _LISTS:
        getattr(options, _LISTS[arg]).append(_take(args, index, arg))
        return index + 2
    if arg in _VALUES:
        name, convert = _VALUES[arg]
        setattr(options, name, convert(_take(args, index, arg)))
        return index + 2
    if arg in _FLAGS:
        setattr(options, _FLAGS[arg], True)
        return index + 1
    if arg.startswith("-"):
        raise ValueError(f"Unknown option: {arg}")
    options.positionals.append(arg)
    return index + 1


def _usage_error(message: str) -> Options:
    return Options(action="help", message=f"{message}\n\n{HELP}", exit_code=1)


def parse_args(argv: list[str] | None = None) -> Options:
    args = list(sys.argv[1:] if argv is None else argv)
    if any(arg in {"-h", "--help"} for arg in args):
        return Options(action="help", message=HELP, exit_code=0)
    options = Options(action="analyze")
    index = 0
    try:
        while index < len(args):
            index = _apply(options, args, index)
    except ValueError as exc:
        return _usage_error(str(exc))
    message = _conflict(options)
    return _usage_error(message) if message else options


# A full-tree refresh replaces every entry, so it must see the whole tree, freshly.
_NOT_WITH_FULL_TREE = (
    ("path arguments", "positionals"),
    ("--changed", "changed"),
    ("--use-existing-coverage", "use_existing_coverage"),
)


def _conflict(options: Options) -> str | None:
    if options.no_coverage and options.coverage_command:
        return "--no-coverage cannot be combined with --coverage-command"
    errors = [error for error in map(exclusion_error, options.exclusions) if error]
    if errors:
        return errors[0]
    if options.full_tree:
        for flag, name in _NOT_WITH_FULL_TREE:
            if getattr(options, name):
                return f"--full-tree cannot be combined with {flag}"
    return None


def _positionals(root: Path, args: list[str]) -> tuple[list[Path], list[str]]:
    existing: list[Path] = []
    filters: list[str] = []
    for arg in args:
        candidate = Path(arg)
        if not candidate.is_absolute():
            candidate = root / arg
        if candidate.exists():
            existing.append(candidate.resolve())
        else:
            filters.append(arg)
    return existing, filters


def _changed_source(root: Path) -> list[Path]:
    return [path for path in changed_files(root) if is_source_file(path)]


def _in_scope(files: list[Path], roots: list[Path]) -> list[Path]:
    """Source roots narrow every other selection to the files under them."""

    return under_roots(files, roots) if roots else files


def _shown(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()


def _explicit_files(existing: list[Path], root: Path, exclusions: list[str], roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in existing:
        if path.is_dir():
            files.extend(_in_scope(iter_source_files([path]), roots))
        elif not _in_scope([path], roots):
            print(f"Skipping {_shown(path, root)}; it is outside the source roots", file=sys.stderr)
        elif excluded(path, root, exclusions):
            print(f"Skipping excluded {_shown(path, root)}", file=sys.stderr)
        elif language_of(path) is not None:
            files.append(path)
    return files


def _selected(options: Options, root: Path, existing: list[Path]) -> list[Path]:
    roots = [(root / path).resolve() for path in options.source_roots]
    if options.changed:
        return _in_scope(_changed_source(root), roots)
    if existing:
        return _explicit_files(existing, root, options.exclusions, roots)
    return iter_source_files(roots or [root])


def _apply_filters(files: list[Path], filters: list[str]) -> list[Path]:
    if not filters:
        return files
    return [path for path in files if any(item in path.as_posix() for item in filters)]


def select_files(options: Options) -> list[Path]:
    root = options.project_root.resolve()
    if options.full_tree:
        for value in options.source_roots or [str(root)]:
            source_root = (root / value).resolve()
            if not source_root.exists():
                raise ValueError(f"Source root does not exist: {source_root}")
            if not source_root.is_relative_to(root):
                raise ValueError(f"Source root must be inside project root: {source_root}")
    existing, filters = _positionals(root, options.positionals)
    files = _apply_filters(_selected(options, root, existing), filters)
    files = exclude_files(files, root, options.exclusions)
    return sorted({path.resolve() for path in files}, key=lambda path: path.as_posix())


UNSUPPORTED_PLATFORM = (
    "crapper does not support native Windows. Run it inside WSL2 with the "
    "project under ~/; see the README."
)


def platform_warning(platform: str, root: Path) -> str | None:
    """Refuse native Windows; suggest the Linux filesystem under WSL2."""

    if platform == "win32":
        return UNSUPPORTED_PLATFORM
    if root.as_posix().startswith("/mnt/"):
        return (
            f"{root} is on a Windows drive. Under WSL2, clone the project under ~/ "
            "for speed and working symlinks."
        )
    return None


def _threshold_exceeded(entries, threshold: float | None) -> bool:
    scored = [entry.crap for entry in entries if entry.crap is not None]
    if threshold is None or not scored or max(scored) <= threshold:
        return False
    print(f"CRAP threshold exceeded: {max(scored):.1f} > {threshold:.1f}", file=sys.stderr)
    return True


def run(argv: list[str] | None = None) -> int:
    options = parse_args(argv)
    if options.action == "help":
        stream = sys.stdout if options.exit_code == 0 else sys.stderr
        print(options.message, file=stream, end="" if options.message.endswith("\n") else "\n")
        return options.exit_code

    root = options.project_root.resolve()
    warning = platform_warning(sys.platform, root)
    if warning:
        print(warning, file=sys.stderr)
    if sys.platform == "win32":
        return 1
    try:
        files = select_files(options)
    except (OSError, ValueError) as exc:
        print(f"Source selection failed: {exc}", file=sys.stderr)
        return 1
    if not files and not options.full_tree and not options.coverage_command:
        print("No source files to analyze.")
        return 0

    bundle = None
    if not options.no_coverage and (files or options.coverage_command):
        if not options.use_existing_coverage:
            try:
                bundle = run_coverage(
                    root, files, options.coverage_command, options.lua_shards, options.full_tree
                )
            except CoverageError as exc:
                print(f"Coverage failed: {exc} {metrics_path(root)} was not replaced.", file=sys.stderr)
                return 1
        if bundle is None:
            bundle = load_bundle(root)

    if not files and not options.full_tree:
        print("No source files to analyze; custom coverage validated.")
        return 0

    if not files:
        print("Publishing empty business tree; no coverage ran." if not options.coverage_command
              else "Publishing empty business tree after custom coverage validation.",
              file=sys.stderr)
    try:
        entries = analyze_files(files, root, bundle)
        report = format_report(entries)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Report rendering failed: {exc}", file=sys.stderr)
        return 1
    try:
        metrics = write_metrics(entries, root)
    except (OSError, UnicodeError) as exc:
        print(f"Snapshot publication failed: {exc}", file=sys.stderr)
        return 1
    print(report, end="", flush=True)
    print(f"Wrote {metrics}", file=sys.stderr)
    return 2 if _threshold_exceeded(entries, options.threshold) else 0


def main(argv: list[str] | None = None) -> None:
    sys.exit(run(argv))
