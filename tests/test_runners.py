import json
import os
import sys

import pytest
from pathlib import Path

from crapper.discover import is_test_file
from crapper.runners import (
    CoverageError,
    _clean_clojure,
    _clean_dir,
    _coverage_report,
    _ensure_python_module,
    _ensure_vitest_coverage,
    _lua_version,
    _manifest_snapshot,
    _modules_with,
    _needs_vitest_provider,
    _prepare_report,
    _python_executable,
    _python_kind,
    _record_lua_lcov,
    _record_python_lcov,
    _restore_manifests,
    _rust_kind,
    _vitest_version,
    python_coverage_commands,
    python_roots,
    python_sources,
    run_coverage,
    run_shell,
    rust_coverage_command,
    rust_modules,
    typescript_command,
    typescript_packages,
    uses_pytest,
)


def _write(root: Path, relative: str, source: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def test_vitest_uses_its_own_coverage_instead_of_c8(tmp_path):
    _write(
        tmp_path,
        "package.json",
        json.dumps({"scripts": {"test": "vitest run"}}),
    )
    _write(tmp_path, "vitest.config.ts", "export default {}\n")
    source = _write(tmp_path, "src/book.ts", "export function plan() { return 1 }\n")
    _write(tmp_path, "src/book.test.ts", "import { plan } from './book'\n")
    report_dir = tmp_path / "target" / "coverage" / "typescript"
    command = typescript_command(tmp_path, [source, tmp_path / "src" / "book.test.ts"], report_dir)
    assert command is not None
    assert "c8" not in command
    assert command[:3] == ["npx", "vitest", "run"]
    assert "--coverage.reporter=lcov" in command
    assert f"--coverage.reportsDirectory={report_dir}" in command
    assert "--coverage.include=src/book.ts" in command
    assert "book.test.ts" not in command


def test_existing_coverage_script_is_left_alone(tmp_path):
    _write(
        tmp_path,
        "package.json",
        json.dumps({"scripts": {"coverage": "vitest run --coverage", "test": "vitest run"}}),
    )
    command = typescript_command(tmp_path, [], tmp_path / "coverage")
    assert command == ["npm", "run", "coverage"]


def test_node_test_script_still_uses_c8(tmp_path):
    _write(tmp_path, "package.json", json.dumps({"scripts": {"test": "node --test"}}))
    report_dir = tmp_path / "target" / "coverage" / "typescript"
    command = typescript_command(tmp_path, [], report_dir)
    assert command == ["npx", "--yes", "c8", "--reporter=lcov", "--reports-dir", str(report_dir), "npm", "test"]


def test_typescript_package_is_found_from_a_source_file(tmp_path):
    _write(tmp_path, "web/package.json", json.dumps({"scripts": {"test": "vitest run"}}))
    source = _write(tmp_path, "web/src/app.ts", "export const n = 1\n")
    assert typescript_packages(tmp_path, [source]) == [(tmp_path / "web").resolve()]


def test_rust_module_is_the_nearest_cargo_package(tmp_path):
    _write(tmp_path, "src-tauri/Cargo.toml", '[package]\nname = "bookwriter"\nversion = "0.1.0"\n')
    lib = _write(tmp_path, "src-tauri/src/lib.rs", "pub fn read_text() {}\n")
    assert rust_modules(tmp_path, [lib]) == [(tmp_path / "src-tauri").resolve()]


def test_java_and_go_modules_keep_the_path_under_the_project_root(tmp_path, symlink):
    _write(tmp_path, "real/pom.xml", "<project/>\n")
    source = _write(tmp_path, "real/src/A.java", "class A {}\n")
    symlink(tmp_path / "linked", tmp_path / "real")
    linked = tmp_path / "linked" / "src" / "A.java"
    assert _modules_with([linked, source], "java", "pom.xml", tmp_path) == [
        tmp_path / "linked",
        tmp_path / "real",
    ]

def test_manifests_are_restored_after_a_coverage_install(tmp_path):
    package_json = tmp_path / "package.json"
    lock = tmp_path / "package-lock.json"
    package_json.write_bytes(b'{"name":"demo"}\n')
    lock.write_bytes(b'{"lockfileVersion":3}\n')
    snapshot = _manifest_snapshot(tmp_path)
    package_json.write_bytes(b'{"name":"changed"}\n')
    lock.unlink()
    shrink = tmp_path / "npm-shrinkwrap.json"
    shrink.write_text("{}\n", encoding="utf-8")
    _restore_manifests(snapshot)
    assert package_json.read_bytes() == b'{"name":"demo"}\n'
    assert lock.read_bytes() == b'{"lockfileVersion":3}\n'
    assert not shrink.exists()


def test_python_project_uses_pytest_and_coverage_lcov(tmp_path):
    _write(tmp_path, "pyproject.toml", "[tool.pytest.ini_options]\ntestpaths = ['tests']\n")
    source = _write(tmp_path, "src/demo/box.py", "def choose(x):\n    return x\n")
    _write(tmp_path, "tests/test_box.py", "def test_choose():\n    assert True\n")
    assert python_roots(tmp_path, [source]) == [tmp_path.resolve()]
    assert uses_pytest(tmp_path)
    assert python_sources(tmp_path, [source, tmp_path / "tests" / "test_box.py"]) == "src"
    assert is_test_file(tmp_path / "tests" / "test_box.py")
    data = tmp_path / "target" / "coverage" / "python" / ".coverage"
    report = data.parent / "lcov.info"
    run, lcov = python_coverage_commands("python3", "pytest", data, report, "src")
    assert run == ["python3", "-m", "coverage", "run", f"--data-file={data}", "--source=src", "-m", "pytest"]
    assert lcov == ["python3", "-m", "coverage", "lcov", f"--data-file={data}", "-o", str(report)]


def test_python_package_without_pytest_uses_unittest(tmp_path):
    _write(tmp_path, "setup.py", "from setuptools import setup\nsetup(name='demo')\n")
    source = _write(tmp_path, "demo/app.py", "def run():\n    return 1\n")
    assert not uses_pytest(tmp_path)
    run, _lcov = python_coverage_commands("python3", "unittest", Path("data"), Path("out"), "demo")
    assert run[-5:] == ["-m", "unittest", "discover", "-s", "."]
    assert python_roots(tmp_path, [source]) == [tmp_path.resolve()]


def test_rust_lcov_path_is_absolute():
    report = Path("/tmp/bookwriter/target/coverage/rust/src-tauri/lcov.info")
    command = rust_coverage_command("llvm-cov", report)
    assert command == ["cargo", "llvm-cov", "--lcov", "--output-path", str(report)]


def test_vitest_version_and_provider_install(tmp_path, monkeypatch):
    assert _vitest_version(tmp_path) is None
    _write(tmp_path, "node_modules/vitest/package.json", "{not json")
    assert _vitest_version(tmp_path) is None
    _write(tmp_path, "node_modules/vitest/package.json", '{"version": "4.1.11"}\n')
    assert _vitest_version(tmp_path) == "4.1.11"
    provider = tmp_path / "node_modules" / "@vitest" / "coverage-v8"
    provider.mkdir(parents=True)
    assert _ensure_vitest_coverage(tmp_path) is True
    provider.rmdir()
    monkeypatch.setattr("crapper.runners.run_shell", lambda command, cwd: 1)
    assert _ensure_vitest_coverage(tmp_path) is False


def test_clean_clojure_keeps_other_language_reports(tmp_path):
    kept = tmp_path / "target" / "coverage" / "python"
    kept.mkdir(parents=True)
    (kept / "lcov.info").write_text("keep", encoding="utf-8")
    stale = tmp_path / "target" / "coverage" / "lcov.info"
    stale.write_text("old", encoding="utf-8")
    _clean_clojure(tmp_path)
    assert (kept / "lcov.info").is_file()
    assert not stale.exists()


def test_python_executable_prefers_a_project_venv(tmp_path):
    import venv

    venv.EnvBuilder(with_pip=False).create(tmp_path / ".venv")
    binary = tmp_path / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    assert _python_executable(tmp_path) == str(binary)


def test_python_executable_without_a_venv_is_the_hosts_python(tmp_path):
    # Windows has no python3.exe outside the Microsoft Store alias.
    assert _python_executable(tmp_path) == ("python" if os.name == "nt" else "python3")


def test_rust_kind_installs_llvm_cov_when_it_is_missing(monkeypatch):
    installed = {"llvm": False}

    def which(name):
        if name == "cargo-llvm-cov" and installed["llvm"]:
            return "/bin/cargo-llvm-cov"
        if name == "rustup":
            return "/bin/rustup"
        return None

    def shell(command, cwd):
        if command[:2] == ["cargo", "install"]:
            installed["llvm"] = True
        return 0

    monkeypatch.setattr("crapper.runners.shutil.which", which)
    monkeypatch.setattr("crapper.runners.run_shell", shell)
    assert _rust_kind() == "llvm-cov"
    installed["llvm"] = False
    monkeypatch.setattr("crapper.runners.run_shell", lambda command, cwd: 1)
    assert _rust_kind() is None


def test_coverage_commands_are_issued_per_language(tmp_path, monkeypatch):
    commands: list[str | list[str]] = []

    def shell(command, cwd):
        commands.append(command)
        if isinstance(command, str):
            return 0
        data_flag = next((part for part in command if part.startswith("--data-file=")), None)
        if data_flag is not None:
            data = Path(data_flag.split("=", 1)[1])
            data.parent.mkdir(parents=True, exist_ok=True)
            data.write_text("x", encoding="utf-8")
            return 1
        if command[0] in ("clj", "mvn", "go", "cargo") or "vitest" in command:
            return 1
        return 0

    monkeypatch.setattr("crapper.runners.run_shell", shell)
    monkeypatch.setattr(
        "crapper.runners.shutil.which",
        lambda name: "/bin/cargo-llvm-cov" if name == "cargo-llvm-cov" else None,
    )
    _write(tmp_path, "deps.edn", "{}\n")
    _write(tmp_path, "src/demo/core.clj", "(ns demo.core)\n(defn x [])\n")
    _write(tmp_path, "pom.xml", "<project></project>\n")
    _write(tmp_path, "src/Board.java", "class Board { int place(){ return 1; } }\n")
    (tmp_path / "target" / "jacoco.exec").parent.mkdir(parents=True)
    (tmp_path / "target" / "jacoco.exec").write_text("x", encoding="utf-8")
    _write(tmp_path, "go.mod", "module example.com/demo\n")
    _write(tmp_path, "board.go", "package demo\nfunc Place() int { return 1 }\n")
    profile = tmp_path / "target" / "coverage" / "go" / "coverage.out"
    profile.parent.mkdir(parents=True)
    profile.write_text("old", encoding="utf-8")
    _write(tmp_path, "package.json", json.dumps({"scripts": {"test": "vitest run"}}))
    _write(tmp_path, "src/ui.ts", "export function view(){ return 1 }\n")
    (tmp_path / "node_modules" / "@vitest" / "coverage-v8").mkdir(parents=True)
    _write(tmp_path, "pyproject.toml", "[tool.pytest.ini_options]\n")
    _write(tmp_path, "src/app.py", "def run():\n    return 1\n")
    _write(tmp_path, "Cargo.toml", '[package]\nname = "demo"\nversion = "0.1.0"\n')
    _write(tmp_path, "src/lib.rs", "pub fn open() {}\n")
    files = [
        tmp_path / "src/demo/core.clj",
        tmp_path / "src/Board.java",
        tmp_path / "board.go",
        tmp_path / "src/ui.ts",
        tmp_path / "src/app.py",
        tmp_path / "src/lib.rs",
    ]
    run_coverage(tmp_path, files, None)
    assert all(isinstance(command, list) for command in commands)
    text = "\n".join(" ".join(command) for command in commands)
    assert "clj -M:cov --lcov" in text
    assert "clj -M:cov\n" in text or text.endswith("clj -M:cov") or "clj -M:cov" in text
    assert "mvn " in text
    assert "go test" in text
    assert "vitest" in text
    assert "coverage run" in text
    assert "cargo llvm-cov" in text
    with pytest.raises(CoverageError, match="LCOV report"):
        run_coverage(tmp_path, files, "echo custom")
    assert commands[-1] == "echo custom"


def test_missing_build_files_skip_that_language(tmp_path, monkeypatch):
    monkeypatch.setattr("crapper.runners.run_shell", lambda command, cwd: 0)
    files = [
        _write(tmp_path, "src/App.java", "class App {}\n"),
        _write(tmp_path, "main.go", "package main\n"),
        _write(tmp_path, "src/app.ts", "export const n = 1\n"),
        _write(tmp_path, "src/lib.rs", "pub fn open() {}\n"),
    ]
    run_coverage(tmp_path, files, None)


def test_run_shell_returns_the_child_status_and_reports_a_failed_start(tmp_path, monkeypatch, capsys):
    assert run_shell("exit 3", tmp_path) == 3
    assert run_shell(["crapper-no-such-program-5"], tmp_path) == 127
    assert "failed to start" in capsys.readouterr().err


def test_argv_executes_interpreter_and_script_paths_with_spaces(tmp_path):
    # A real executable, rather than a mock, exercises exec argument handling.
    # The venv keeps Python's runtime intact.
    import venv

    environment = tmp_path / "python environment"
    venv.EnvBuilder(with_pip=False).create(environment)
    executable = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    script = _write(tmp_path, "script directory/check arguments.py",
                    "import sys\nassert sys.argv[1:] == ['path with spaces', 'a&b', \"quote'word\", 'ünï']\n")
    assert run_shell([str(executable), str(script), "path with spaces", "a&b", "quote'word", "ünï"], tmp_path) == 0


def _launcher(directory: Path, name: str, status: int) -> Path:
    """An executable `name` on this host that exits `status`: a .cmd on Windows, as npm and npx are."""

    if os.name == "nt":
        return _write(directory, f"{name}.cmd", f"@exit /b {status}\r\n")
    launcher = _write(directory, name, f"#!/bin/sh\nexit {status}\n")
    launcher.chmod(0o755)
    return launcher


def test_argv_finds_a_launcher_on_path_by_its_bare_name(tmp_path, monkeypatch):
    tools = tmp_path / "tool directory"
    _launcher(tools, "argv-tool", 7)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    assert run_shell(["argv-tool", "path with spaces"], tmp_path) == 7


def test_custom_shell_command_preserves_redirection(tmp_path):
    with pytest.raises(CoverageError, match="LCOV report"):
        run_coverage(tmp_path, [], 'echo shell-output > "custom report.txt"')
    assert (tmp_path / "custom report.txt").read_text().strip() == "shell-output"


def test_clean_removes_a_stale_directory_and_ignores_a_missing_one(tmp_path):
    stale = tmp_path / "target" / "coverage" / "old"
    stale.mkdir(parents=True)
    (stale / "lcov.info").write_text("x", encoding="utf-8")
    kept = tmp_path / "target" / "coverage" / "python"
    kept.mkdir()
    _clean_clojure(tmp_path)
    assert not stale.exists()
    assert kept.is_dir()
    _clean_dir(tmp_path / "target" / "coverage")
    assert not (tmp_path / "target" / "coverage").exists()
    _clean_clojure(tmp_path)
    _clean_dir(tmp_path / "missing")


def test_package_json_without_a_test_script_has_no_coverage_command(tmp_path):
    assert typescript_command(tmp_path, [], tmp_path / "cov") is None
    _write(tmp_path, "package.json", "{")
    assert typescript_command(tmp_path, [], tmp_path / "cov") is None
    _write(tmp_path, "package.json", "[]")
    assert typescript_command(tmp_path, [], tmp_path / "cov") is None
    _write(tmp_path, "package.json", '{"scripts": ["nope"]}')
    assert typescript_command(tmp_path, [], tmp_path / "cov") is None


def test_vitest_include_skips_a_file_outside_the_package(tmp_path):
    from crapper.runners import _vitest_includes

    package = tmp_path / "web"
    _write(package, "src/app.ts", "export const n = 1\n")
    outside = Path("/tmp/crapper-not-in-package.ts")
    assert _vitest_includes(package, [package / "src" / "app.ts", outside]) == ["src/app.ts"]


def test_nested_module_coverage_report_is_slugged(tmp_path):
    module = tmp_path / "src-tauri"
    module.mkdir()
    report = _coverage_report(tmp_path, module, "rust")
    assert report == tmp_path / "target" / "coverage" / "rust" / "src-tauri" / "lcov.info"


def test_prepare_report_replaces_an_existing_directory(tmp_path):
    report = tmp_path / "target" / "coverage" / "typescript" / "lcov.info"
    report.parent.mkdir(parents=True)
    (report.parent / "old.info").write_text("old", encoding="utf-8")
    _prepare_report(report)
    assert report.parent.is_dir()
    assert not (report.parent / "old.info").exists()


def test_pytest_detection_and_the_unittest_fallback(tmp_path, monkeypatch, capsys):
    assert uses_pytest(tmp_path) is False
    _write(tmp_path, "conftest.py", "")
    assert uses_pytest(tmp_path) is True
    monkeypatch.setattr("crapper.runners.run_shell", lambda *_args: 0)
    assert _python_kind("python3", tmp_path) == "pytest"
    bare = tmp_path / "bare"
    bare.mkdir()
    assert _python_kind("python3", bare) == "unittest"
    monkeypatch.setattr("crapper.runners._ensure_python_module", lambda *_args: False)
    assert _python_kind("python3", tmp_path) == "unittest"
    assert "Falling back to unittest" in capsys.readouterr().err


def test_python_sources_ignore_a_file_outside_the_package(tmp_path):
    inside = _write(tmp_path, "src/app.py", "def run():\n    return 1\n")
    assert python_sources(tmp_path, [inside, Path("/tmp/crapper-outside.py")]) == "src"


def test_missing_python_module_is_installed(tmp_path, monkeypatch):
    commands = []

    def shell(command, _cwd):
        commands.append(command)
        if command[:2] == ["python3", "-c"]:
            return 1
        return 0

    monkeypatch.setattr("crapper.runners.run_shell", shell)
    assert _ensure_python_module("python3", tmp_path, "coverage") is True
    assert any(command[1:3] == ["-m", "pip"] for command in commands)
    commands.clear()

    def already(_command, _cwd):
        commands.append("probe")
        return 0

    monkeypatch.setattr("crapper.runners.run_shell", already)
    assert _ensure_python_module("python3", tmp_path, "coverage") is True
    assert commands == ["probe"]


def test_python_lcov_warnings(tmp_path, monkeypatch, capsys):
    data = tmp_path / ".coverage"
    monkeypatch.setattr("crapper.runners.run_shell", lambda *_args: 1)
    assert _record_python_lcov(tmp_path, 1, data, "lcov") is False
    assert "Python coverage exited 1" in capsys.readouterr().err
    assert _record_python_lcov(tmp_path, 0, data, "lcov") is False
    assert capsys.readouterr().err == ""
    data.write_text("x", encoding="utf-8")
    assert _record_python_lcov(tmp_path, 0, data, "lcov") is False
    assert "coverage lcov exited 1" in capsys.readouterr().err
    monkeypatch.setattr("crapper.runners.run_shell", lambda *_args: 0)
    assert _record_python_lcov(tmp_path, 7, data, "lcov") is False
    assert "Coverage was still recorded" in capsys.readouterr().err
    assert _record_python_lcov(tmp_path, 0, data, "lcov") is True


def test_custom_coverage_command_failure_stops_execution(tmp_path):
    with pytest.raises(CoverageError, match="Coverage command exited 9"):
        run_coverage(tmp_path, [], "exit 9")


def test_clojure_without_a_build_file_is_skipped(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("crapper.runners.run_shell", lambda *_args: 0)
    source = _write(tmp_path, "src/demo/core.clj", "(ns demo.core)\n(defn x [] 1)\n")
    run_coverage(tmp_path, [source], None)
    assert "skipping Clojure coverage" in capsys.readouterr().err


def test_rust_kind_none_skips_rust_coverage(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("crapper.runners._rust_kind", lambda: None)
    monkeypatch.setattr("crapper.runners.run_shell", lambda *_args: 0)
    source = _write(tmp_path, "src/lib.rs", "pub fn open() {}\n")
    _write(tmp_path, "Cargo.toml", '[package]\nname = "demo"\nversion = "0.1.0"\n')
    assert run_coverage(tmp_path, [source], None) is None
    with pytest.raises(CoverageError, match="No usable coverage for rust"):
        run_coverage(tmp_path, [source], None, strict=True)
    assert "cargo" not in capsys.readouterr().err


def test_tarpaulin_command_and_kind(monkeypatch):
    monkeypatch.setattr(
        "crapper.runners.shutil.which",
        lambda name: "/bin/cargo-tarpaulin" if name == "cargo-tarpaulin" else None,
    )
    assert _rust_kind() == "tarpaulin"
    report = Path("/tmp/book/target/coverage/rust/lcov.info")
    assert "tarpaulin" in rust_coverage_command("tarpaulin", report)


def test_typescript_without_a_test_script_is_skipped(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("crapper.runners.run_shell", lambda *_args: 0)
    _write(tmp_path, "package.json", '{"scripts": {}}')
    source = _write(tmp_path, "src/app.ts", "export const n = 1\n")
    run_coverage(tmp_path, [source], None)
    assert "No package.json test script" in capsys.readouterr().err


def test_vitest_provider_failure_skips_typescript(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("crapper.runners.run_shell", lambda *_args: 1)
    _write(tmp_path, "package.json", '{"scripts": {"test": "vitest run"}}')
    source = _write(tmp_path, "src/app.ts", "export const n = 1\n")
    run_coverage(tmp_path, [source], None)
    assert "coverage provider is missing" in capsys.readouterr().err


def test_python_coverage_module_missing_is_reported(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("crapper.runners._ensure_python_module", lambda *_args: False)
    monkeypatch.setattr("crapper.runners.run_shell", lambda *_args: 0)
    _write(tmp_path, "pyproject.toml", "[tool.pytest.ini_options]\n")
    source = _write(tmp_path, "src/app.py", "def run():\n    return 1\n")
    run_coverage(tmp_path, [source], None)
    assert "coverage is missing" in capsys.readouterr().err


def test_python_without_coverage_is_na_without_an_install(tmp_path, monkeypatch, capsys):
    ran = []
    monkeypatch.setattr("crapper.runners.run_shell", lambda command, cwd: ran.append(command) or 1)
    source = _write(tmp_path, "helper.py", "def run():\n    return 1\n")
    assert run_coverage(tmp_path, [source], None) is None
    assert ran == [["python" if os.name == "nt" else "python3", "-c", "import coverage"]]
    assert "coverage is missing" in capsys.readouterr().err


def _recording_shell(monkeypatch, codes=None):
    calls: list[tuple[list[str], Path]] = []

    def shell(command, cwd):
        calls.append((command, Path(cwd)))
        return (codes or {}).get(command[0], 0)

    monkeypatch.setattr("crapper.runners.run_shell", shell)
    return calls


def test_vitest_provider_install_must_leave_the_provider_on_disk(tmp_path, monkeypatch):
    provider = tmp_path / "node_modules" / "@vitest" / "coverage-v8"

    def install(_command, _cwd):
        provider.mkdir(parents=True)
        return 0

    monkeypatch.setattr("crapper.runners.run_shell", install)
    assert _ensure_vitest_coverage(tmp_path) is True
    provider.rmdir()
    monkeypatch.setattr("crapper.runners.run_shell", lambda _command, _cwd: 0)
    assert _ensure_vitest_coverage(tmp_path) is False


def test_rustup_failure_is_reported_and_llvm_cov_must_land_on_path(monkeypatch, capsys):
    monkeypatch.setattr(
        "crapper.runners.shutil.which", lambda name: "/bin/rustup" if name == "rustup" else None
    )
    _recording_shell(monkeypatch, {"rustup": 1})
    assert _rust_kind() is None
    assert "rustup component add llvm-tools-preview failed." in capsys.readouterr().err
    _recording_shell(monkeypatch)
    assert _rust_kind() is None
    assert "failed" not in capsys.readouterr().err


def test_python_root_is_the_nearest_project_marker(tmp_path):
    _write(tmp_path, "tools/setup.cfg", "")
    nested = _write(tmp_path, "tools/lib/run.py", "def run():\n    return 1\n")
    loose = _write(tmp_path, "scripts/x.py", "def x():\n    return 1\n")
    assert python_roots(tmp_path, [nested, loose]) == [tmp_path.resolve(), (tmp_path / "tools").resolve()]


def test_each_build_tool_runs_only_for_its_own_files(tmp_path, monkeypatch):
    calls = _recording_shell(monkeypatch)
    _write(tmp_path, "api/pom.xml", "<project/>\n")
    java = _write(tmp_path, "api/src/App.java", "class App {}\n")
    _write(tmp_path, "svc/go.mod", "module example.com/svc\n")
    _write(tmp_path, "svc/pom.xml", "<project/>\n")
    go = _write(tmp_path, "svc/main.go", "package main\n")
    run_coverage(tmp_path, [java, go], None)
    assert [cwd for command, cwd in calls if command[0] == "mvn"] == [(tmp_path / "api").resolve()]
    assert [cwd for command, cwd in calls if command[0] == "go"] == [(tmp_path / "svc").resolve()]


def test_clojure_retries_without_lcov_and_reports_a_second_failure(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "deps.edn", "{}\n")
    source = _write(tmp_path, "src/demo/core.clj", "(ns demo.core)\n(defn x [] 1)\n")
    codes = {"clj -M:cov --lcov": 1, "clj -M:cov": 0}
    calls = []

    def shell(command, _cwd):
        calls.append(" ".join(command))
        return codes[calls[-1]]

    monkeypatch.setattr("crapper.runners.run_shell", shell)
    run_coverage(tmp_path, [source], None)
    assert calls == ["clj -M:cov --lcov", "clj -M:cov"]
    assert "Clojure coverage exited" not in capsys.readouterr().err
    codes["clj -M:cov"] = 2
    run_coverage(tmp_path, [source], None)
    assert "Clojure coverage exited 2." in capsys.readouterr().err
    calls.clear()
    codes["clj -M:cov --lcov"] = 0
    run_coverage(tmp_path, [source], None)
    assert calls == ["clj -M:cov --lcov"]
    assert capsys.readouterr().err == ""


def _build_failures(tmp_path, monkeypatch, capsys, files, tool, message):
    """Run twice in a fresh tree: once failing with exit 1, once passing."""

    _recording_shell(monkeypatch, {tool: 1})
    run_coverage(tmp_path, files, None)
    assert message in capsys.readouterr().err
    _recording_shell(monkeypatch)
    run_coverage(tmp_path, files, None)
    assert message.split(" exited")[0] + " exited" not in capsys.readouterr().err


def test_java_failure_is_reported(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "pom.xml", "<project/>\n")
    files = [_write(tmp_path, "src/App.java", "class App {}\n")]
    _build_failures(tmp_path, monkeypatch, capsys, files, "mvn", "Java coverage exited 1 in")


def test_go_profile_directory_is_created_and_a_failure_is_reported(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "go.mod", "module example.com/demo\n")
    files = [_write(tmp_path, "main.go", "package main\n")]
    _build_failures(tmp_path, monkeypatch, capsys, files, "go", "Go coverage exited 1 in")
    assert (tmp_path / "target" / "coverage" / "go").is_dir()


def test_c8_coverage_failure_is_reported_without_a_vitest_install(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "package.json", json.dumps({"scripts": {"test": "node --test"}}))
    files = [_write(tmp_path, "src/app.ts", "export const n = 1\n")]
    _build_failures(tmp_path, monkeypatch, capsys, files, "npx", "TypeScript coverage exited 1 in")
    assert (tmp_path / "target" / "coverage" / "typescript").is_dir()
    calls = _recording_shell(monkeypatch)
    run_coverage(tmp_path, files, None)
    assert [command[0] for command, _cwd in calls] == ["npx"]


def test_rust_report_directory_is_created_and_a_failure_is_reported(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("crapper.runners._rust_kind", lambda: "llvm-cov")
    _write(tmp_path, "Cargo.toml", '[package]\nname = "demo"\nversion = "0.1.0"\n')
    files = [_write(tmp_path, "src/lib.rs", "pub fn open() {}\n")]
    _build_failures(tmp_path, monkeypatch, capsys, files, "cargo", "Rust coverage exited 1 in")
    assert (tmp_path / "target" / "coverage" / "rust").is_dir()


def test_python_report_directory_is_created_on_every_run(tmp_path, monkeypatch):
    calls = _recording_shell(monkeypatch)
    _write(tmp_path, "pyproject.toml", "[project]\nname = 'demo'\n")
    files = [_write(tmp_path, "src/app.py", "def run():\n    return 1\n")]
    run_coverage(tmp_path, files, None)
    run_coverage(tmp_path, files, None)
    assert (tmp_path / "target" / "coverage" / "python").is_dir()
    assert sum("run" in command[2:4] for command, _cwd in calls if command[1:3] == ["-m", "coverage"]) == 2


def test_python_tests_failing_with_exit_one_still_record_coverage(tmp_path, monkeypatch, capsys):
    data = tmp_path / ".coverage"
    data.write_text("x", encoding="utf-8")
    monkeypatch.setattr("crapper.runners.run_shell", lambda *_args: 0)
    _record_python_lcov(tmp_path, 1, data, ["lcov"])
    assert "Python tests exited 1" in capsys.readouterr().err


def test_prepare_report_creates_missing_parents(tmp_path):
    report = tmp_path / "target" / "coverage" / "typescript" / "lcov.info"
    _prepare_report(report)
    assert report.parent.is_dir()


def test_lua_version_is_what_the_interpreter_writes(tmp_path):
    if os.name == "nt":
        binary = _write(tmp_path, "lua.cmd", "@echo Lua 5.4\r\n")
    else:
        binary = _write(tmp_path, "lua", "#!/bin/sh\nprintf 'Lua 5.4\\n'\n")
        binary.chmod(0o755)
    assert _lua_version(str(binary)) == "Lua 5.4"
    assert _lua_version(str(tmp_path / "missing")) == ""


def test_vitest_provider_is_needed_only_for_vitest_commands():
    assert _needs_vitest_provider(["npx", "vitest", "run", "--coverage"]) is True
    assert _needs_vitest_provider(["npm", "run", "coverage"]) is False
    assert _needs_vitest_provider(["npx", "--yes", "c8", "--reporter=lcov", "npm", "test"]) is False
