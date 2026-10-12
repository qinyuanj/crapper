"""Command line for the multi-language CRAP tool."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from crapper.analyze import analyze_files
from crapper.coverage import load_bundle
from crapper.discover import exclude_files, is_test_file, iter_source_files, language_of
from crapper.metrics import write_metrics
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
                                May be repeated.
  --exclude <path-or-glob>      Exclude a project-relative file or directory.
                                May be repeated; applies to every selection mode.
                                Both separators work. * and ? match within a path
                                segment; ** matches zero or more whole segments.
                                Matching directories exclude their descendants.
  --full-tree                   Replace the complete configured business tree.
                                Rejects --changed, positionals and existing coverage.
                                --no-coverage publishes complexity only.
  --changed                     Analyze added and modified source files from
                                git status.
  --no-coverage                 Score complexity only. Coverage and CRAP are N/A.
  --use-existing-coverage       Read coverage already on disk. Do not rerun tests.
  --coverage-command <cmd>      Run this command instead of the per-language
                                coverage tools, then read the reports it wrote.
  --threshold <number>          Exit 2 when the worst CRAP score is above this.

Arguments:
  path              File or directory to analyze. Test paths are included when
                    you name them explicitly.
  filter            When the argument is not a path, only source files whose
                    path contains this text are analyzed.

With no paths, source files under the project root are analyzed. Directories
named test, tests, spec, specs, vendor, node_modules, and target are skipped,
as are *_test.go, *.spec.ts, test_*.py, *_test.py, and *_spec.lua files.

Coverage, when it is produced:
  Clojure      clj -M:cov --lcov, then Cloverage form counts or LCOV
  Java         Maven JaCoCo instruction coverage, for each pom.xml module
  Go           go test ./... -coverprofile=...
  TypeScript   npm run coverage, Vitest's own LCOV, or c8 around npm test
  Rust         cargo llvm-cov, or cargo tarpaulin, in the nearest Cargo.toml
  Python       coverage.py LCOV, via pytest or unittest, per project
  Lua          busted -c with luacov, then luacov -r lcov, per .busted or rockspec

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


def _take(args: list[str], index: int, option: str) -> str:
    if index + 1 >= len(args) or not args[index + 1] or args[index + 1].startswith("-"):
        raise ValueError(f"{option} requires a value")
    return args[index + 1]


def parse_args(argv: list[str] | None = None) -> Options:
    args = list(sys.argv[1:] if argv is None else argv)
    if any(arg in {"-h", "--help"} for arg in args):
        return Options(action="help", message=HELP, exit_code=0)
    options = Options(action="analyze")
    index = 0
    try:
        while index < len(args):
            arg = args[index]
            if arg in {"-s", "--source-root"}:
                options.source_roots.append(_take(args, index, arg))
                index += 2
                continue
            if arg == "--exclude":
                options.exclusions.append(_take(args, index, arg))
                index += 2
                continue
            if arg == "--root":
                options.project_root = Path(_take(args, index, arg))
                index += 2
                continue
            if arg == "--coverage-command":
                options.coverage_command = _take(args, index, arg)
                index += 2
                continue
            if arg == "--threshold":
                raw = _take(args, index, arg)
                try:
                    options.threshold = float(raw)
                except ValueError as exc:
                    raise ValueError("--threshold requires a number") from exc
                index += 2
                continue
            if arg == "--no-coverage":
                options.no_coverage = True
                index += 1
                continue
            if arg == "--use-existing-coverage":
                options.use_existing_coverage = True
                index += 1
                continue
            if arg == "--full-tree":
                options.full_tree = True
                index += 1
                continue
            if arg == "--changed":
                options.changed = True
                index += 1
                continue
            if arg.startswith("-"):
                raise ValueError(f"Unknown option: {arg}")
            options.positionals.append(arg)
            index += 1
    except ValueError as exc:
        return Options(action="help", message=f"{exc}\n\n{HELP}", exit_code=1)
    if options.no_coverage and options.coverage_command:
        return Options(
            action="help",
            message=f"--no-coverage cannot be combined with --coverage-command\n\n{HELP}",
            exit_code=1,
        )
    if options.full_tree and (options.changed or options.positionals or options.use_existing_coverage):
        return Options(
            action="help",
            message="--full-tree cannot be combined with --changed, positional paths/filters, "
                    f"or --use-existing-coverage\n\n{HELP}",
            exit_code=1,
        )
    return options


def _changed_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stderr.strip() or "git status failed", file=sys.stderr)
        return []
    found: list[Path] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        path_text = line[3:].strip()
        if " -> " in path_text:
            path_text = path_text.split(" -> ", 1)[1]
        path_text = path_text.strip('"')
        found.append((root / path_text).resolve())
    return found


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
    return [
        path
        for path in _changed_files(root)
        if language_of(path) is not None and not is_test_file(path)
    ]


def _explicit_files(existing: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in existing:
        if path.is_dir():
            files.extend(iter_source_files([path]))
        elif language_of(path) is not None:
            files.append(path)
    return files


def _selected(options: Options, root: Path, existing: list[Path]) -> list[Path]:
    if options.changed:
        return _changed_source(root)
    if options.source_roots:
        return iter_source_files([(root / path).resolve() for path in options.source_roots])
    if existing:
        return _explicit_files(existing)
    return iter_source_files([root])


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


def platform_warning(platform: str, root: Path) -> str | None:
    """Suggest the Linux filesystem for Windows-mounted projects under WSL2."""

    if platform != "win32" and root.as_posix().startswith("/mnt/"):
        return (
            f"{root} is on a Windows drive. Under WSL2, clone the project under ~/ "
            "for speed and working symlinks."
        )
    return None


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
    try:
        files = select_files(options)
    except (OSError, ValueError) as exc:
        print(f"Source selection failed: {exc}", file=sys.stderr)
        return 1
    if not files and not options.full_tree:
        print("No source files to analyze.")
        return 0
    if not files:
        print("Publishing empty business tree; no coverage ran." if not options.coverage_command
              else "Publishing empty business tree after custom coverage validation.",
              file=sys.stderr)

    bundle = None
    if not options.no_coverage and (files or options.coverage_command):
        if not options.use_existing_coverage:
            try:
                bundle = run_coverage(root, files, options.coverage_command)
            except CoverageError as exc:
                print(f"Coverage failed: {exc}", file=sys.stderr)
                return 1
        if bundle is None:
            bundle = load_bundle(root)

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
    if options.threshold is not None:
        scored = [entry.crap for entry in entries if entry.crap is not None]
        if scored and max(scored) > options.threshold:
            print(
                f"CRAP threshold exceeded: {max(scored):.1f} > {options.threshold:.1f}",
                file=sys.stderr,
            )
            return 2
    return 0


def main(argv: list[str] | None = None) -> None:
    sys.exit(run(argv))
