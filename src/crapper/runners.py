"""Run each language's coverage tool, then leave the reports for the loader.

Custom command and built-in Lua failures stop analysis via CoverageError.
Other language runners warn and leave that language at N/A, unless the run is
strict (`--full-tree`), where any failed language stops it too. Languages whose
coverage succeeded are stamped, so `stale_files` can tell a later tool that
their reports still match the tree.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
import threading
from pathlib import Path

from crapper.coverage import CoverageBundle, load_bundle, read_fresh_lcov
from crapper.discover import is_test_file, language_of
from crapper import platform
from crapper.stamp import STAMP_NAME, fingerprint, record, stale_files  # noqa: F401  stale_files is public here

_MAVEN = [
    "mvn", "-q",
    "org.jacoco:jacoco-maven-plugin:0.8.12:prepare-agent",
    "test",
    "org.jacoco:jacoco-maven-plugin:0.8.12:report",
]


class CoverageError(RuntimeError):
    """Coverage execution failed; analysis must not publish a snapshot."""


def _warn(message: str) -> None:
    print(message, file=sys.stderr)


def run_shell(command: str | list[str], cwd: Path) -> int:
    """Run built-in argv without a shell; custom command strings use a shell."""
    display = command if isinstance(command, str) else shlex.join(command)
    _warn(f"+ ({cwd}) {display}")
    try:
        completed = platform.run(command, cwd)
    except FileNotFoundError as exc:
        missing = command[0] if isinstance(command, list) and not shutil.which(command[0]) else None
        _warn(f"Coverage command failed to start: {missing} is not on PATH" if missing
              else f"Coverage command failed to start: {exc}")
        return 127
    except OSError as exc:
        _warn(f"Coverage command failed to start: {exc}")
        return 127
    return completed.returncode


def _nearest(start: Path, marker: str, stop: Path) -> Path | None:
    current = start if start.is_dir() else start.parent
    stop = stop.resolve()
    while True:
        if (current / marker).is_file():
            return current
        if current == stop or current.parent == current:
            return None
        current = current.parent


def _modules_with(files: list[Path], language: str, marker: str, root: Path) -> list[Path]:
    found = (_nearest(Path(file), marker, root) for file in files if language_of(file) == language)
    return sorted({module for module in found if module is not None})


def _wrote(report: Path | None, language: str) -> bool:
    if report is None or report.is_file():
        return True
    _warn(f"{language} coverage wrote no {report}. {language} coverage will be N/A.")
    return False


def _run_reported(command: list[str], module: Path, language: str, report: Path | None = None) -> bool:
    """True when `command` exits 0 and writes `report`. None means the command picks its own location."""

    code = run_shell(command, module)
    if code != 0:
        _warn(f"{language} coverage exited {code} in {module}. {language} coverage will be N/A.")
        return False
    return _wrote(report, language)


def _clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _clean_clojure(root: Path) -> None:
    coverage = root / "target" / "coverage"
    if not coverage.is_dir():
        return
    keep = {"typescript", "rust", "go", "python", "lua", STAMP_NAME}
    for path in coverage.iterdir():
        if path.name in keep:
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


_VITEST_CONFIGS = (
    "vitest.config.ts",
    "vitest.config.mts",
    "vitest.config.cts",
    "vitest.config.js",
    "vitest.config.mjs",
    "vitest.config.cjs",
)


def _package_json(package: Path) -> dict | None:
    path = package / "package.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _uses_vitest(package: Path, scripts: dict) -> bool:
    if "vitest" in str(scripts.get("test", "")):
        return True
    return any((package / name).is_file() for name in _VITEST_CONFIGS)


def _vitest_version(package: Path) -> str | None:
    installed = package / "node_modules" / "vitest" / "package.json"
    if installed.is_file():
        try:
            data = json.loads(installed.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("version"), str):
            return data["version"]
    return None


def _manifest_snapshot(package: Path) -> dict[Path, bytes | None]:
    snapshot: dict[Path, bytes | None] = {}
    for name in ("package.json", "package-lock.json", "npm-shrinkwrap.json"):
        path = package / name
        snapshot[path] = path.read_bytes() if path.is_file() else None
    return snapshot


def _restore_manifests(snapshot: dict[Path, bytes | None]) -> None:
    for path, content in snapshot.items():
        if content is None:
            if path.is_file():
                path.unlink()
        elif not path.is_file() or path.read_bytes() != content:
            path.write_bytes(content)


def _ensure_vitest_coverage(package: Path) -> bool:
    """Vitest collects coverage itself. c8 never sees its worker processes."""

    if (package / "node_modules" / "@vitest" / "coverage-v8").is_dir():
        return True
    version = _vitest_version(package)
    spec = f"@vitest/coverage-v8@{version}" if version else "@vitest/coverage-v8"
    _warn(
        f"Installing {spec} so Vitest can write LCOV. "
        "package.json and the lockfile are restored afterward."
    )
    snapshot = _manifest_snapshot(package)
    code = run_shell(["npm", "install", "--no-save", "--no-package-lock", spec], package)
    _restore_manifests(snapshot)
    ready = code == 0 and (package / "node_modules" / "@vitest" / "coverage-v8").is_dir()
    if not ready:
        _warn("Vitest's coverage provider is missing. TypeScript coverage will be N/A.")
    return ready


def typescript_packages(root: Path, files: list[Path]) -> list[Path]:
    packages = _modules_with(files, "typescript", "package.json", root.resolve())
    return sorted({package.resolve() for package in packages})


_NPM_COVERAGE = ["npm", "run", "coverage"]  # the script picks its own report location


def typescript_command(package: Path, files: list[Path], report_dir: Path) -> list[str] | None:
    """Argv that writes LCOV into `report_dir`, or None without a test script."""

    data = _package_json(package)
    if data is None:
        return None
    scripts = data.get("scripts") or {}
    if not isinstance(scripts, dict):
        scripts = {}
    if "coverage" in scripts:
        return list(_NPM_COVERAGE)
    if _uses_vitest(package, scripts):
        return _vitest_coverage_command(package, files, report_dir)
    if "test" not in scripts:
        return None
    return ["npx", "--yes", "c8", "--reporter=lcov", "--reports-dir", str(report_dir), "npm", "test"]


def _vitest_includes(package: Path, files: list[Path]) -> list[str]:
    includes: list[str] = []
    package = package.resolve()
    for file in files:
        if language_of(file) != "typescript" or is_test_file(file):
            continue
        try:
            relative = Path(file).resolve().relative_to(package)
        except ValueError:
            continue
        includes.append(relative.as_posix())
    return includes


def _vitest_coverage_command(package: Path, files: list[Path], report_dir: Path) -> list[str]:
    binary = package / "node_modules" / ".bin" / "vitest"
    command = [str(binary)] if binary.is_file() else ["npx", "vitest"]
    command.extend([
        "run",
        "--coverage",
        "--coverage.reporter=lcov",
        f"--coverage.reportsDirectory={report_dir}",
        "--coverage.reportOnFailure=true",
    ])
    for include in _vitest_includes(package, files):
        command.append(f"--coverage.include={include}")
    return command


def _coverage_report(root: Path, module: Path, language: str) -> Path:
    root = root.resolve()
    module = module.resolve()
    if module == root:
        return root / "target" / "coverage" / language / "lcov.info"
    slug = module.relative_to(root).as_posix().replace("/", "__")
    return root / "target" / "coverage" / language / slug / "lcov.info"


def rust_modules(root: Path, files: list[Path]) -> list[Path]:
    modules = _modules_with(files, "rust", "Cargo.toml", root.resolve())
    return sorted({module.resolve() for module in modules})


def _install_llvm_cov() -> bool:
    if shutil.which("rustup") and run_shell(["rustup", "component", "add", "llvm-tools-preview"], Path.home()) != 0:
        _warn("rustup component add llvm-tools-preview failed.")
    code = run_shell(["cargo", "install", "cargo-llvm-cov", "--locked"], Path.home())
    return code == 0 and shutil.which("cargo-llvm-cov") is not None


def _rust_kind() -> str | None:
    if shutil.which("cargo-llvm-cov"):
        return "llvm-cov"
    if shutil.which("cargo-tarpaulin"):
        return "tarpaulin"
    _warn("Neither cargo-llvm-cov nor cargo-tarpaulin is installed. Installing cargo-llvm-cov.")
    if _install_llvm_cov():
        return "llvm-cov"
    _warn("Rust coverage will be N/A.")
    return None


_PYTHON_MARKERS = ("pyproject.toml", "setup.cfg", "setup.py", "pytest.ini", "tox.ini")


def _python_root(file: Path, root: Path) -> Path:
    for marker in _PYTHON_MARKERS:
        module = _nearest(file, marker, root)
        if module is not None:
            return module
    return root


def python_roots(root: Path, files: list[Path]) -> list[Path]:
    root = root.resolve()
    sources = [Path(file) for file in files if Path(file).suffix == ".py"]
    return sorted({_python_root(file, root).resolve() for file in sources})


def _python_executable(package: Path) -> str:
    for venv in (".venv", "venv"):
        candidate = package / venv / platform.VENV_PYTHON
        if candidate.is_file():
            return str(candidate)
    return platform.DEFAULT_PYTHON


def uses_pytest(package: Path) -> bool:
    if (package / "pytest.ini").is_file() or (package / "conftest.py").is_file():
        return True
    for name in ("pyproject.toml", "setup.cfg", "tox.ini"):
        path = package / name
        if path.is_file() and "pytest" in path.read_text(encoding="utf-8", errors="replace"):
            return True
    return False


def python_sources(package: Path, files: list[Path]) -> str:
    """Directories coverage.py should report even when a file was never imported."""

    tops: set[str] = set()
    package = package.resolve()
    for file in files:
        path = Path(file)
        if path.suffix != ".py" or is_test_file(path):
            continue
        try:
            relative = path.resolve().relative_to(package)
        except ValueError:
            continue
        tops.add(relative.parts[0])
    return ",".join(sorted(tops))


def python_coverage_commands(
    py: str, kind: str, data_file: Path, report: Path, source: str
) -> tuple[list[str], list[str]]:
    run = [py, "-m", "coverage", "run", f"--data-file={data_file}"]
    if source:
        run.append(f"--source={source}")
    if kind == "pytest":
        run.extend(["-m", "pytest"])
    else:
        run.extend(["-m", "unittest", "discover", "-s", "."])
    lcov = [py, "-m", "coverage", "lcov", f"--data-file={data_file}", "-o", str(report)]
    return run, lcov


def _ensure_python_module(py: str, package: Path, module: str, install: bool = True) -> bool:
    """install=False turns ensure into a probe: report, but never touch the interpreter."""
    if run_shell([py, "-c", "import " + module], package) == 0:
        return True
    if not install:
        return False
    _warn(
        f"Installing {module} for Python coverage. "
        "The project requirements are left unchanged."
    )
    code = run_shell(
        [py, "-m", "pip", "install", "--disable-pip-version-check", module],
        package,
    )
    return code == 0


def rust_coverage_command(kind: str, report: Path) -> list[str]:
    if kind == "llvm-cov":
        return ["cargo", "llvm-cov", "--lcov", "--output-path", str(report)]
    return ["cargo", "tarpaulin", "--out", "Lcov", "--output-dir", str(report.parent)]


def _cover_clojure(root: Path, _files: list[Path]) -> bool:
    if not (root / "deps.edn").is_file() and not (root / "bb.edn").is_file():
        _warn("No deps.edn or bb.edn; skipping Clojure coverage.")
        return False
    _clean_clojure(root)
    code = run_shell(["clj", "-M:cov", "--lcov"], root)
    if code != 0:
        _warn("clj -M:cov --lcov failed; retrying without --lcov.")
        code = run_shell(["clj", "-M:cov"], root)
    if code != 0:
        _warn(f"Clojure coverage exited {code}. Clojure coverage will be N/A.")
    return code == 0


def _cover_java(root: Path, files: list[Path]) -> bool:
    modules = _modules_with(files, "java", "pom.xml", root)
    if not modules:
        _warn("No pom.xml; skipping Java coverage.")
        return False
    results = []
    for module in modules:
        report = module / "target" / "site" / "jacoco"
        _clean_dir(report)
        (module / "target" / "jacoco.exec").unlink(missing_ok=True)
        results.append(_run_reported(_MAVEN, module, "Java", report / "jacoco.xml"))
    return all(results)


def _cover_go(root: Path, files: list[Path]) -> bool:
    modules = _modules_with(files, "go", "go.mod", root)
    if not modules:
        _warn("No go.mod; skipping Go coverage.")
        return False
    results = []
    for module in modules:
        profile = module / "target" / "coverage" / "go" / "coverage.out"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.unlink(missing_ok=True)
        command = ["go", "test", "./...", f"-coverprofile={profile}"]
        results.append(_run_reported(command, module, "Go", profile))
    return all(results)


def _prepare_report(report: Path) -> None:
    _clean_dir(report.parent)
    report.parent.mkdir(parents=True, exist_ok=True)


def _cover_typescript_package(root: Path, package: Path, files: list[Path]) -> bool:
    report = _coverage_report(root, package, "typescript")
    command = typescript_command(package, files, report.parent)
    if command is None:
        _warn(f"No package.json test script in {package}; skipping TypeScript coverage.")
        return False
    if _needs_vitest_provider(command) and not _ensure_vitest_coverage(package):
        return False
    _prepare_report(report)
    expected = None if command == _NPM_COVERAGE else report
    ok = _run_reported(command, package, "TypeScript", expected)
    if ok and expected is not None:
        # Vitest/c8 write SF relative to the package; anchor them like Lua.
        absolute_lcov_sources(expected, package)
    return ok


def _cover_typescript(root: Path, files: list[Path]) -> bool:
    packages = typescript_packages(root, files)
    if not packages:
        _warn("No package.json; skipping TypeScript coverage.")
        return False
    return all([_cover_typescript_package(root, package, files) for package in packages])


def _needs_vitest_provider(command: list[str]) -> bool:
    return any(Path(part).name == "vitest" for part in command)


def _python_kind(py: str, package: Path) -> str:
    if not uses_pytest(package):
        return "unittest"
    if _ensure_python_module(py, package, "pytest"):
        return "pytest"
    _warn(f"pytest is missing in {package}. Falling back to unittest.")
    return "unittest"


def _record_python_lcov(package: Path, code: int, data_file: Path, lcov_cmd: list[str]) -> bool:
    """True only when the tests passed and the LCOV conversion worked."""

    if not data_file.exists():
        if code != 0:
            _warn(f"Python coverage exited {code} in {package}. Python coverage will be N/A.")
        return False
    lcov_code = run_shell(lcov_cmd, package)
    if lcov_code != 0:
        _warn(f"coverage lcov exited {lcov_code} in {package}. Python coverage will be N/A.")
        return False
    if code != 0:
        _warn(f"Python tests exited {code} in {package}. Coverage was still recorded.")
    return code == 0


def _cover_python_package(root: Path, package: Path, files: list[Path]) -> bool:
    report = _coverage_report(root, package, "python")
    py = _python_executable(package)
    # Probe rather than install: a Lua tree with a few helper scripts should
    # not pay for, or change its environment through, a failing install.
    if not _ensure_python_module(py, package, "coverage", False):
        _warn(f"coverage is missing in {package}; {py} cannot import it. Python coverage will be N/A.")
        return False
    report.parent.mkdir(parents=True, exist_ok=True)
    data_file = report.parent / ".coverage"
    report.unlink(missing_ok=True)
    data_file.unlink(missing_ok=True)
    run_cmd, lcov_cmd = python_coverage_commands(
        py, _python_kind(py, package), data_file, report, python_sources(package, files)
    )
    recorded = _record_python_lcov(package, run_shell(run_cmd, package), data_file, lcov_cmd)
    return recorded and _wrote(report, "Python")


def _cover_python(root: Path, files: list[Path]) -> bool:
    return all([_cover_python_package(root, package, files) for package in python_roots(root, files)])


def _cover_rust(root: Path, files: list[Path]) -> bool:
    modules = rust_modules(root, files)
    if not modules:
        _warn("No Cargo.toml; skipping Rust coverage.")
        return False
    kind = _rust_kind()
    if kind is None:
        return False
    results = []
    for module in modules:
        report = _coverage_report(root, module, "rust")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.unlink(missing_ok=True)
        results.append(_run_reported(rust_coverage_command(kind, report), module, "Rust", report))
    return all(results)


_LUA_VERSION = "Lua 5.4"
# LuaCov switches to cluacov's C line hook and active-line analysis whenever it
# loads. Both change which lines count, so coverage requires one pinned version.
_CLUACOV_VERSION = "1.0.0"
_LUA_EXCLUDE = ("^/", "^%a:", "_spec$", "^spec/", "/spec/", "^lua_modules/", "^%.luarocks/", "^target/")


def _nearest_lua_root(directory: Path, stop: Path, found: dict[Path, Path | None]) -> Path | None:
    """Memoized per directory, so sibling files share one walk."""

    if directory not in found:
        if (directory / ".busted").is_file() or any(directory.glob("*.rockspec")):
            found[directory] = directory
        elif directory == stop or directory.parent == directory:
            found[directory] = None
        else:
            found[directory] = _nearest_lua_root(directory.parent, stop, found)
    return found[directory]


def lua_modules(root: Path, files: list[Path]) -> dict[Path, list[Path]]:
    """Lua files grouped by their nearest `.busted` or rockspec directory, else the project root."""

    root = root.resolve()
    found: dict[Path, Path | None] = {}
    modules: dict[Path, list[Path]] = {}
    for file in files:
        if Path(file).suffix != ".lua":
            continue
        module = _nearest_lua_root(Path(file).resolve().parent, root, found)
        modules.setdefault((module or root).resolve(), []).append(file)
    return dict(sorted(modules.items()))




def _lua_version(binary: str) -> str:
    try:
        completed = platform.run([binary, "-e", "io.write(_VERSION)"], Path.cwd(), capture=True)
    except OSError:
        return ""
    return completed.stdout.strip()


def lua_interpreter() -> str | None:
    """An absolute path to a Lua 5.4 interpreter: `lua5.4` first, then `lua`."""

    for name in ("lua5.4", "lua"):
        binary = shutil.which(name)
        if binary and _lua_version(binary) == _LUA_VERSION:
            return str(Path(binary).resolve())
    return None


def _lua_string(text: str) -> str:
    equals = "=="
    while "]" + equals + "]" in text:
        equals += "="
    return "[" + equals + "[" + text + "]" + equals + "]"


def _lua_include(relative: str) -> list[str]:
    """Anchored LuaCov patterns for one source; LuaCov drops `.lua` before matching."""

    name = re.sub(r"([\^$()%.\[\]*+\-?])", r"%\1", relative.removesuffix(".lua"))
    # cluacov's hook keeps the `./` prefix that LuaCov's own hook strips.
    return ["^" + name + "$", "^%./" + name + "$"]


def lua_coverage_config(
    stats: Path, report: Path, source_files: list[Path] | None = None, directory: Path | None = None,
) -> str:
    directory = (directory or Path.cwd()).resolve()
    selected = sorted({os.path.relpath(Path(path).resolve(), directory).replace("\\", "/")
                       for path in (source_files or [])})
    untested = ", ".join(_lua_string(path) for path in selected)
    # Record only the selected sources: spec and support files are never published.
    include = ", ".join(_lua_string(pattern) for path in selected for pattern in _lua_include(path))
    exclude = ", ".join(_lua_string(pattern) for pattern in _LUA_EXCLUDE)
    return (
        "return {\n"
        f"  statsfile = {_lua_string(str(stats))},\n"
        f"  reportfile = {_lua_string(str(report))},\n"
        f"  includeuntestedfiles = {{{untested}}},\n"
        f"  include = {{{include}}},\n"
        f"  exclude = {{{exclude}}},\n"
        "}\n"
    )


def lua_cluacov_version(lua: str) -> str:
    """The cluacov version the interpreter loads, or "" when it cannot load one."""

    loader = "pcall(require, 'luarocks.loader'); "
    try:
        completed = platform.run(
            [lua, "-e", loader + "local ok, v = pcall(require, 'cluacov.version'); "
             "if ok and type(v) == 'string' then io.write(v) end"],
            Path.cwd(), capture=True,
        )
    except OSError:
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def require_lua_cluacov(lua: str) -> None:
    """Stop Lua coverage unless `lua` loads the pinned cluacov version."""

    cluacov = lua_cluacov_version(lua)
    if cluacov != _CLUACOV_VERSION:
        found = f"found {cluacov}" if cluacov else "not installed"
        raise CoverageError(f"Lua coverage requires cluacov {_CLUACOV_VERSION} for {lua} ({found}): "
                            f"luarocks --lua-version=5.4 install cluacov {_CLUACOV_VERSION}-1")


def lua_coverage_commands(lua: str, config: Path) -> tuple[list[str], list[str]]:
    # Run both tools in the selected interpreter. Busted's --lua re-execution
    # joins arguments into a shell string, so module entrypoints avoid it.
    loader = "pcall(require, 'luarocks.loader'); "
    # Busted exits on failure but returns on success in non-standalone mode.
    # Stop Lua before it treats the argv placeholder "busted" as a script.
    run = [lua, "-e", loader + "require('busted.runner')({standalone = false}); os.exit(0)",
           "--", "busted", "--ignore-lua", "-c", f"--coverage-config-file={config}"]
    lcov = [lua, "-e", loader +
            "local r = require('luacov.runner'); local c = r.load_config(arg[1]); "
            # LuaCov retains slash variants as different keys, including zero-hit
            # selected files, and cluacov's hook keeps the `./` that LuaCov's own
            # hook strips. Normalize before the reporter merges those keys. A
            # Windows Lua names one file with both separators.
            "local real_name = r.real_name; local windows = package.config:sub(1, 1) == string.char(92); "
            "r.real_name = function(name) name = real_name(name); "
            "if windows then name = name:gsub(string.char(92), '/') end; "
            "return (name:gsub('^%./', '')) end; "
            "c.reporter = 'lcov'; r.run_report(c); os.exit(0)",
            "--", "luacov", str(config)]
    return run, lcov


def absolute_lcov_sources(report: Path, module: Path) -> None:
    """luacov writes paths relative to where busted ran. Anchor them to that directory."""

    lines = []
    for line in report.read_text(encoding="utf-8").splitlines():
        if line.startswith("SF:") and not Path(line[3:]).is_absolute():
            line = "SF:" + (module / line[3:]).as_posix()
        lines.append(line)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _record_lua_lcov(module: Path, code: int, stats: Path, report: Path, lcov_cmd: str | list[str]) -> None:
    if code != 0:
        raise CoverageError(f"Lua tests exited {code} in {module}.")
    if not stats.is_file():
        raise CoverageError(f"LuaCov stats missing after Lua tests in {module}: {stats}")
    lcov_code = run_shell(lcov_cmd, module)
    if lcov_code != 0:
        raise CoverageError(f"LuaCov LCOV conversion exited {lcov_code} in {module}.")
    if not report.is_file():
        raise CoverageError(f"LuaCov LCOV report missing after conversion in {module}: {report}")
    try:
        absolute_lcov_sources(report, module)
    except (OSError, UnicodeError) as exc:
        raise CoverageError(f"LuaCov LCOV normalization failed in {module}: {exc}") from exc


# Busted's own option parsing and file loader list the spec files a single run
# would load, so the shards' union is exactly that run's selection.
_BUSTED_SPECS = (
    "pcall(require, 'luarocks.loader'); "
    "local options = require('pl.tablex').update(require('busted.options'), {standalone = false}); "
    "local args = assert(require('busted.modules.cli')(options):parse({'--ignore-lua'})); "
    "local path = require('pl.path'); assert(path.chdir(path.normpath(args.directory))); "
    "local _, _, list = require('busted.modules.test_file_loader')(require('busted.core')(), args.loaders); "
    "for _, file in ipairs(list(args.ROOT, args.pattern, "
    "{excludes = args['exclude-pattern'], recursive = args.recursive})) do io.write(file, '\\n') end"
)


def run_captured(command: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run argv with its output held, so concurrent runs do not interleave."""
    try:
        return platform.run(command, cwd, capture=True)
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, "", f"Coverage command failed to start: {exc}\n")


def lua_spec_files(lua: str, module: Path) -> list[str]:
    """Spec files, relative to Busted's directory, per the module's Busted configuration."""

    completed = run_captured([lua, "-e", _BUSTED_SPECS], module)
    if completed.returncode != 0:
        _warn(f"Cannot list Busted spec files in {module}; running one coverage shard.\n{completed.stderr}")
        return []
    return [line for line in completed.stdout.splitlines() if line]


def shard_specs_by_size(specs: list[str], count: int, base: Path) -> list[list[str]]:
    """Split specs into coverage shards by size; Busted's order within each shard."""

    def size(spec: str) -> int:
        try:
            return (base / spec).stat().st_size
        except OSError:
            return 0

    order = {spec: index for index, spec in enumerate(specs)}
    shards: list[list[str]] = [[] for _ in range(count)]
    loads = [0] * count
    for spec in sorted(specs, key=lambda spec: (-size(spec), order[spec])):
        lightest = min(range(count), key=lambda index: (loads[index], index))
        shards[lightest].append(spec)
        loads[lightest] += size(spec)
    return [sorted(shard, key=order.__getitem__) for shard in shards]


def merge_luacov_stats(parts: list[Path], target: Path) -> None:
    """Sum LuaCov line hits per file and line, in LuaCov's own stats format."""

    merged: dict[bytes, list[int]] = {}
    for part in parts:
        lines = part.read_bytes().split(b"\n")
        for header, counts in zip(lines[0::2], lines[1::2]):
            _, _, name = header.partition(b":")
            hits = merged.setdefault(name, [])
            for index, count in enumerate(counts.split()):
                if index == len(hits):
                    hits.append(0)
                hits[index] += int(count)
    with target.open("wb") as stream:
        for name in sorted(merged):
            hits = merged[name]
            stream.write(b"%d:%s\n" % (len(hits), name))
            stream.write(b"".join(b"%d " % count for count in hits) + b"\n")


def _shard_artifacts(directory: Path) -> list[Path]:
    return sorted(directory.glob("luacov.shard-*"))


def _write_lua_config(config: Path, stats: Path, report: Path, selected: list[Path], module: Path) -> None:
    try:
        config.write_text(lua_coverage_config(stats, report, selected, module), encoding="utf-8", newline="\n")
    except OSError as exc:
        raise CoverageError(f"Cannot prepare LuaCov config: {exc}") from exc


def _run_lua_shards(lua: str, module: Path, report: Path, selected: list[Path],
                    groups: list[list[str]], stats: Path, lcov_cmd: list[str]) -> None:
    """Run each coverage shard under its own stats file, then sum them into `stats`."""

    shards = [(report.parent / f"luacov.shard-{index}.cfg.lua",
               report.parent / f"luacov.shard-{index}.stats.out") for index in range(1, len(groups) + 1)]
    try:
        commands = []
        for (config, shard_stats), group in zip(shards, groups):
            _write_lua_config(config, shard_stats, report, selected, module)
            commands.append(lua_coverage_commands(lua, config)[0] + group)
        _warn(f"+ ({module}) Busted in {len(groups)} coverage shards: "
              f"{shlex.join(lcov_cmd[:1])} -e '<busted runner>' -- busted …")
        stop, finished = threading.Event(), threading.Event()
        results, errors = [], []

        def execute(command):
            if stop.is_set():
                return None
            with platform.cancellable(stop):
                return run_captured(command, module)

        def collect():
            try:
                with ThreadPoolExecutor(len(commands)) as pool:
                    results.extend(pool.map(execute, commands))
            except BaseException as error:
                errors.append(error)
            finally:
                finished.set()

        # Only this coordinator waits on the pool. The main thread polls an
        # Event, avoiding Windows' uninterruptible locks and interrupted-join
        # bookkeeping (bpo-45274). Its completion includes pool shutdown.
        coordinator = threading.Thread(target=collect, name="crapper-coverage")
        try:
            coordinator.start()
            while not finished.wait(0.2):
                pass
        except BaseException:
            stop.set()
            while not finished.wait(0.2):
                pass
            raise
        if errors:
            raise errors[0]
        for completed in results:
            sys.stdout.write(completed.stdout)
            sys.stderr.write(completed.stderr)
        sys.stdout.flush()
        for index, completed in enumerate(results, 1):
            if completed.returncode != 0:
                raise CoverageError(f"Lua tests exited {completed.returncode} in coverage shard "
                                    f"{index} of {len(groups)} in {module}.")
        for _, shard_stats in shards:
            if not shard_stats.is_file():
                raise CoverageError(f"LuaCov stats missing after Lua tests in {module}: {shard_stats}")
        try:
            merge_luacov_stats([shard_stats for _, shard_stats in shards], stats)
        except (OSError, ValueError) as exc:
            stats.unlink(missing_ok=True)
            raise CoverageError(f"Cannot sum LuaCov stats in {module}: {exc}") from exc
    finally:
        _invalidate_artifacts(_shard_artifacts(report.parent))


def _cover_lua(root: Path, files: list[Path], reports: list[tuple[Path, Path]], shards: int) -> dict:
    lua = lua_interpreter()
    if lua is None:
        raise CoverageError("No Lua 5.4 interpreter (lua5.4 or lua) on PATH for Lua coverage.")
    require_lua_cluacov(lua)
    modules = lua_modules(root, files)
    fresh = {}
    for module, report in reports:
        stats = report.parent / "luacov.stats.out"
        config = report.parent / "luacov.cfg.lua"
        selected = modules[module]
        try:
            report.parent.mkdir(parents=True, exist_ok=True)
            _write_lua_config(config, stats, report, selected, module)
        except OSError as exc:
            raise CoverageError(f"Cannot prepare LuaCov config: {exc}") from exc
        run_cmd, lcov_cmd = lua_coverage_commands(lua, config)
        specs = lua_spec_files(lua, module) if shards > 1 else []
        count = min(shards, len(specs))
        if count > 1:
            # Shards ran all specs and summed their stats into `stats`.
            _run_lua_shards(lua, module, report, selected,
                            shard_specs_by_size(specs, count, module), stats, lcov_cmd)
            code = 0
        else:
            code = run_shell(run_cmd, module)
        _record_lua_lcov(module, code, stats, report, lcov_cmd)
        fresh.update(_fresh_lcov(report, module))
    return fresh


def _fresh_lcov(report: Path, source_base: Path) -> dict:
    try:
        return read_fresh_lcov(report, source_base)
    except (OSError, UnicodeError, ValueError) as exc:
        raise CoverageError(f"Required LCOV report invalid or unavailable: {report}: {exc}") from exc


def _invalidate_artifacts(paths: list[Path]) -> None:
    try:
        for path in paths:
            path.unlink(missing_ok=True)
    except OSError as exc:
        raise CoverageError(f"Cannot invalidate managed coverage artifact: {exc}") from exc


# Lua runs last and raises instead of returning False; see `_cover_lua`.
_BUILT_IN = (
    ("clojure", _cover_clojure),
    ("java", _cover_java),
    ("go", _cover_go),
    ("typescript", _cover_typescript),
    ("python", _cover_python),
    ("rust", _cover_rust),
)


def _run_custom(root: Path, command: str) -> CoverageBundle:
    report = root / "coverage" / "lcov.info"
    _invalidate_artifacts([report, report.parent / "luacov.stats.out", root / "luacov.stats.out"])
    code = run_shell(command, root)
    if code != 0:
        raise CoverageError(f"Coverage command exited {code} in {root}.")
    return CoverageBundle(fresh_lcov=_fresh_lcov(report, root), source_root=root)


def _run_built_in(root: Path, files: list[Path], lua_shards: int | None, failed: list[str]) -> CoverageBundle | None:
    """Append each language whose coverage failed to `failed`. Lua failures also raise."""

    languages = {language_of(file) for file in files} - {None}
    lua_reports = [(module, _coverage_report(root, module, "lua")) for module in lua_modules(root, files)]
    if len({report for _, report in lua_reports}) != len(lua_reports):
        raise CoverageError("Lua modules have colliding managed report paths.")
    _invalidate_artifacts([path for _, report in lua_reports
                           for path in (report, report.parent / "luacov.stats.out",
                                        *_shard_artifacts(report.parent))])
    failed.extend(language for language, cover in _BUILT_IN if language in languages and not cover(root, files))
    if "lua" not in languages:
        return None
    try:
        fresh = _cover_lua(root, files, lua_reports, lua_shards or os.cpu_count() or 1)
    except CoverageError:
        failed.append("lua")
        raise
    bundle = load_bundle(root) if languages - {"lua"} else CoverageBundle()
    bundle.managed_lua = fresh
    bundle.source_root = root
    bundle.lcov.update(fresh)
    return bundle


def run_coverage(
    root: Path, files: list[Path], command: str | None, lua_shards: int | None = None,
    strict: bool = False,
) -> CoverageBundle | None:
    """Return validated custom/Lua coverage; other runners retain legacy loading.

    Lua coverage runs in `lua_shards` coverage shards, by default one per CPU.
    A custom command or Lua failure raises CoverageError; so does any other
    failed language when `strict`. Every language that did not fail is stamped,
    unless the tree changed while coverage ran.
    """

    root = root.resolve()
    before = fingerprint(root)
    present = sorted({language_of(file) for file in files} - {None})
    failed: list[str] = []
    try:
        if command:
            failed.extend(present)
            bundle = _run_custom(root, command)
            failed.clear()
        else:
            bundle = _run_built_in(root, files, lua_shards, failed)
    finally:
        record(root, files, command, failed, before)
    if strict and failed:
        raise CoverageError(f"No usable coverage for {', '.join(sorted(failed))}.")
    return bundle
