import json
import os
import re
from pathlib import Path

import pytest
from host_shell import chain, python

from crapper.runners import CoverageError, _clean_clojure, run_coverage, stale_files

STAMP = Path("target/coverage/crapper-stamp.json")
# A custom command must leave a valid coverage/lcov.info with at least one record.
# Both files live in coverage/, which the fingerprint skips.
OK = python("from pathlib import Path; d = Path('coverage'); d.mkdir(exist_ok=True); "
            "(d / 'x').write_bytes(b''); "
            "(d / 'lcov.info').write_bytes(b'SF:coverage/x\\nLF:0\\nLH:0\\nend_of_record\\n')")


def _append(name: str, text: str = "x\\n") -> str:
    """A command that appends `text` to `name`, creating its directory."""

    return python(f"from pathlib import Path; p = Path('{name}'); p.parent.mkdir(parents=True, exist_ok=True); "
                  f"p.open('a').write('{text}')")


def _failed(root: Path, files: list[Path], command: str | None = None) -> list[str]:
    """The languages a strict (--full-tree) run names as failed; [] when it succeeds."""

    try:
        run_coverage(root, files, command, None, True)
    except CoverageError as exc:
        return re.fullmatch(r"No usable coverage for (.*)\.", str(exc)).group(1).split(", ")
    return []


def _text(command) -> str:
    return command if isinstance(command, str) else " ".join(command)


def _write(root: Path, relative: str, text: str = "x\n") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _report_named_in(command, cwd: Path) -> None:
    command = _text(command)
    for flag in ("--data-file=", "-coverprofile=", "--output-path ", " -o "):
        if flag in command:
            _write(Path(command.split(flag, 1)[1].split()[0]), "", "")
    for flag in ("--reports-dir ", "--coverage.reportsDirectory="):
        if flag in command:
            _write(Path(command.split(flag, 1)[1].split()[0]), "lcov.info", "")
    if command.startswith("mvn "):
        _write(cwd, "target/site/jacoco/jacoco.xml", "<report/>")


def _succeed(command, cwd: Path) -> int:
    _report_named_in(command, cwd)
    return 0


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr("crapper.runners.run_shell", _succeed)
    monkeypatch.setattr(
        "crapper.runners.shutil.which",
        lambda name: "/bin/cargo-llvm-cov" if name == "cargo-llvm-cov" else None,
    )
    _write(tmp_path, "deps.edn", "{}\n")
    _write(tmp_path, "pom.xml", "<project/>\n")
    _write(tmp_path, "go.mod", "module example.com/demo\n")
    _write(tmp_path, "package.json", json.dumps({"scripts": {"test": "node --test"}}))
    _write(tmp_path, "pyproject.toml", "[tool.pytest.ini_options]\n")
    _write(tmp_path, "Cargo.toml", '[package]\nname = "demo"\n')
    _write(tmp_path, "tests/test_app.py", "def test_run():\n    pass\n")
    return tmp_path


def _sources(root: Path) -> list[Path]:
    names = ["src/core.clj", "src/Board.java", "board.go", "src/ui.ts", "src/app.py", "src/lib.rs"]
    return [_write(root, name) for name in names]


def test_every_language_that_produced_a_report_succeeds_and_is_stamped(project):
    files = _sources(project)
    assert _failed(project, files) == []
    stamp = json.loads((project / STAMP).read_text(encoding="utf-8"))
    assert stamp["version"] == 1
    assert stamp["command"] is None
    assert sorted(stamp["languages"]) == ["clojure", "go", "java", "python", "rust", "typescript"]
    assert len(set(stamp["languages"].values())) == 1
    assert stale_files(project, files, None) == []


def test_failed_languages_are_returned_sorted_and_lose_their_stamp(project, monkeypatch):
    files = _sources(project)
    run_coverage(project, files, None)

    def shell(command, cwd):
        if command[0] in {"go", "cargo"}:
            return 1
        return _succeed(command, cwd)

    monkeypatch.setattr("crapper.runners.run_shell", shell)
    assert _failed(project, files) == ["go", "rust"]
    stamp = json.loads((project / STAMP).read_text(encoding="utf-8"))
    assert sorted(stamp["languages"]) == ["clojure", "java", "python", "typescript"]
    assert stale_files(project, files, None) == [project / "board.go", project / "src/lib.rs"]


@pytest.mark.parametrize(
    ("failing", "language"),
    [
        (lambda command: command.startswith("clj"), "clojure"),
        (lambda command: command.startswith("mvn"), "java"),
        (lambda command: command.startswith("npx"), "typescript"),
        (lambda command: " -m coverage run " in command, "python"),
        (lambda command: " -m coverage lcov " in command, "python"),
    ],
)
def test_a_non_zero_exit_fails_that_language(project, monkeypatch, failing, language):
    def shell(command, cwd):
        _report_named_in(command, cwd)
        return 1 if failing(_text(command)) else 0

    monkeypatch.setattr("crapper.runners.run_shell", shell)
    assert _failed(project, _sources(project)) == [language]


@pytest.mark.parametrize(
    ("missing", "language"),
    [
        ("-coverprofile=", "go"),
        ("--output-path ", "rust"),
        ("mvn ", "java"),
        (" -o ", "python"),
        ("--reports-dir ", "typescript"),
    ],
)
def test_a_command_that_writes_no_report_fails_that_language(project, monkeypatch, capsys, missing, language):
    def shell(command, cwd):
        if missing not in _text(command):
            _report_named_in(command, cwd)
        return 0

    monkeypatch.setattr("crapper.runners.run_shell", shell)
    assert _failed(project, _sources(project)) == [language]
    assert "coverage will be N/A" in capsys.readouterr().err


def test_missing_build_files_and_tools_fail_their_language(tmp_path, monkeypatch):
    monkeypatch.setattr("crapper.runners.run_shell", _succeed)
    monkeypatch.setattr("crapper.runners.shutil.which", lambda _name: None)
    monkeypatch.setattr("crapper.runners._install_llvm_cov", lambda: False)
    files = [_write(tmp_path, name) for name in ("a.clj", "A.java", "a.go", "a.ts", "a.rs")]
    assert _failed(tmp_path, files) == ["clojure", "go", "java", "rust", "typescript"]
    assert not (tmp_path / "target").exists()


def test_a_run_with_nothing_to_stamp_says_nothing_about_the_stamp(tmp_path, capsys):
    with pytest.raises(CoverageError, match="Coverage command exited 1"):
        run_coverage(tmp_path, [_write(tmp_path, "a.py")], "exit 1")
    assert "stamp" not in capsys.readouterr().err
    assert not (tmp_path / "target").exists()


def test_the_stamp_is_written_with_sorted_keys(project):
    run_coverage(project, _sources(project), None)
    text = (project / STAMP).read_text(encoding="utf-8")
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"


def test_typescript_without_a_test_script_or_provider_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("crapper.runners.run_shell", _succeed)
    _write(tmp_path, "package.json", json.dumps({"scripts": {}}))
    assert _failed(tmp_path, [_write(tmp_path, "a.ts")]) == ["typescript"]
    _write(tmp_path, "package.json", json.dumps({"scripts": {"test": "vitest run"}}))
    monkeypatch.setattr("crapper.runners._ensure_vitest_coverage", lambda _package: False)
    assert _failed(tmp_path, [tmp_path / "a.ts"]) == ["typescript"]


def test_python_without_coverage_installed_fails(project, monkeypatch):
    monkeypatch.setattr("crapper.runners._ensure_python_module", lambda *_args: False)
    assert _failed(project, [_write(project, "src/app.py")]) == ["python"]


def test_lua_is_stamped_only_when_its_coverage_succeeded(tmp_path, monkeypatch):
    monkeypatch.setattr("crapper.runners._cover_lua", lambda *_args: {})
    files = [_write(tmp_path, "a.lua")]
    run_coverage(tmp_path, files, None)
    assert stale_files(tmp_path, files, None) == []

    def broken(*_args):
        raise CoverageError("Lua tests exited 1.")

    monkeypatch.setattr("crapper.runners._cover_lua", broken)
    with pytest.raises(CoverageError, match="Lua tests exited 1"):
        run_coverage(tmp_path, files, None)
    assert stale_files(tmp_path, files, None) == files


def test_a_custom_command_records_every_language_it_was_given(tmp_path):
    files = [_write(tmp_path, "a.py"), _write(tmp_path, "b.go"), _write(tmp_path, "notes.md")]
    assert _failed(tmp_path, files, OK) == []
    stamp = json.loads((tmp_path / STAMP).read_text(encoding="utf-8"))
    assert stamp["command"] == OK
    assert sorted(stamp["languages"]) == ["go", "python"]
    assert stale_files(tmp_path, files[:2], OK) == []
    assert stale_files(tmp_path, files[:2], None) == files[:2]
    assert stale_files(tmp_path, files[:2], "other") == files[:2]
    with pytest.raises(CoverageError, match="exited 3"):
        run_coverage(tmp_path, files, "exit 3")
    assert not (tmp_path / STAMP).exists()


def test_a_new_command_discards_entries_recorded_for_the_old_one(tmp_path):
    first, second = _write(tmp_path, "a.py"), _write(tmp_path, "b.go")
    run_coverage(tmp_path, [first], OK)
    other = chain(OK, python("'other'"))
    run_coverage(tmp_path, [second], other)
    stamp = json.loads((tmp_path / STAMP).read_text(encoding="utf-8"))
    assert stamp["command"] == other
    assert sorted(stamp["languages"]) == ["go"]


def test_any_change_outside_skipped_directories_makes_coverage_stale(tmp_path):
    files = [_write(tmp_path, "src/a.py")]
    test = _write(tmp_path, "tests/test_a.py")
    run_coverage(tmp_path, files, OK)
    _write(tmp_path, "target/other.txt")
    _write(tmp_path, ".venv/lib/x.py")
    _write(tmp_path, "node_modules/x/index.js")
    assert stale_files(tmp_path, files, OK) == []
    os.utime(test, ns=(1, 1))
    assert stale_files(tmp_path, files, OK) == files


def test_size_and_new_files_change_the_fingerprint(tmp_path):
    source = _write(tmp_path, "a.py")
    run_coverage(tmp_path, [source], OK)
    stat = source.stat()
    source.write_text("longer\n", encoding="utf-8")
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert stale_files(tmp_path, [source], OK) == [source]
    run_coverage(tmp_path, [source], OK)
    _write(tmp_path, "docs/new.txt")
    assert stale_files(tmp_path, [source], OK) == [source]


def test_a_renamed_file_changes_the_fingerprint(tmp_path):
    source = _write(tmp_path, "a.py")
    other = _write(tmp_path, "b.txt")
    run_coverage(tmp_path, [source], OK)
    stat = other.stat()
    renamed = other.rename(tmp_path / "c.txt")
    os.utime(renamed, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert stale_files(tmp_path, [source], OK) == [source]


def test_files_outside_the_covered_set_are_stale(tmp_path):
    covered, other = _write(tmp_path, "a.py"), _write(tmp_path, "b.py")
    run_coverage(tmp_path, [covered], OK)
    assert stale_files(tmp_path, [covered, other], OK) == [other]
    assert stale_files(tmp_path, [other, covered], OK) == [other]


@pytest.mark.parametrize(
    "spoil",
    [
        lambda stamp: stamp.update(version=2),
        lambda stamp: stamp.update(languages=[]),
        lambda stamp: stamp.update(files=[]),
        lambda stamp: stamp.pop("files"),
        lambda stamp: stamp["files"].update(python="a.py"),
        lambda stamp: stamp["languages"].update(python=5),
        lambda stamp: stamp.clear(),
    ],
)
def test_an_invalid_stamp_makes_everything_stale(tmp_path, spoil):
    source = _write(tmp_path, "a.py")
    run_coverage(tmp_path, [source], OK)
    stamp = json.loads((tmp_path / STAMP).read_text(encoding="utf-8"))
    spoil(stamp)
    (tmp_path / STAMP).write_text(json.dumps(stamp), encoding="utf-8")
    assert stale_files(tmp_path, [source], OK) == [source]


@pytest.mark.parametrize("data", [b"not json", b"[]", b"\xff"])
def test_an_unreadable_stamp_makes_everything_stale(tmp_path, data):
    source = _write(tmp_path, "a.py")
    _write(tmp_path, str(STAMP)).write_bytes(data)
    assert stale_files(tmp_path, [source], OK) == [source]


def test_a_missing_stamp_makes_everything_stale(tmp_path):
    source = _write(tmp_path, "a.py")
    assert stale_files(tmp_path, [source], None) == [source]


def test_an_invalid_stamp_is_replaced_by_the_next_run(tmp_path):
    source = _write(tmp_path, "a.py")
    _write(tmp_path, str(STAMP), "not json")
    assert _failed(tmp_path, [source], OK) == []
    assert stale_files(tmp_path, [source], OK) == []


def test_a_version_mismatch_is_not_merged_into_the_new_stamp(tmp_path):
    source = _write(tmp_path, "a.py")
    old = {"version": 2, "command": OK, "languages": {"go": "x"}, "files": {"go": ["b.go"]}}
    _write(tmp_path, str(STAMP), json.dumps(old))
    run_coverage(tmp_path, [source], OK)
    assert sorted(json.loads((tmp_path / STAMP).read_text(encoding="utf-8"))["languages"]) == ["python"]


def test_a_stamp_that_cannot_be_written_is_reported_and_left_whole(tmp_path, monkeypatch, capsys):
    source = _write(tmp_path, "a.py")
    run_coverage(tmp_path, [source], OK)
    before = (tmp_path / STAMP).read_text(encoding="utf-8")

    def broken(*_args):
        raise OSError("disk full")

    monkeypatch.setattr("crapper.atomic.os.replace", broken)
    _write(tmp_path, "b.py")
    assert _failed(tmp_path, [source], OK) == []
    assert "Could not record the coverage stamp: disk full" in capsys.readouterr().err
    assert (tmp_path / STAMP).read_text(encoding="utf-8") == before
    assert sorted(path.name for path in (tmp_path / STAMP).parent.iterdir()) == ["crapper-stamp.json"]
    assert stale_files(tmp_path, [source], OK) == [source]


def test_clojure_cleanup_keeps_the_stamp(tmp_path):
    stamp = _write(tmp_path, str(STAMP), "{}")
    _clean_clojure(tmp_path)
    assert stamp.is_file()


def test_a_dangling_symlink_does_not_break_the_fingerprint(tmp_path, symlink):
    source = _write(tmp_path, "a.py")
    symlink(tmp_path / "dangling", tmp_path / "missing")
    assert _failed(tmp_path, [source], OK) == []
    assert stale_files(tmp_path, [source], OK) == []
    (tmp_path / "dangling").unlink()
    symlink(tmp_path / "dangling", source)
    assert stale_files(tmp_path, [source], OK) == [source]


def test_a_file_edited_while_coverage_runs_is_not_stamped(tmp_path, capsys):
    source = _write(tmp_path, "src/a.py")
    _write(tmp_path, "tests/test_a.py")
    command = chain(OK, _append("tests/test_a.py", "more\\n"))
    assert _failed(tmp_path, [source], command) == []
    assert "Files changed while coverage ran" in capsys.readouterr().err
    assert stale_files(tmp_path, [source], command) == [source]
    assert not (tmp_path / STAMP).exists()


def test_an_edit_during_a_run_drops_the_entries_that_run_replaced(tmp_path):
    first, second = _write(tmp_path, "a.py"), _write(tmp_path, "b.go")
    command = chain(OK, python("from pathlib import Path; "
                               "Path('go.edit').exists() and Path('b.go').open('a').write('x\\n')"))
    run_coverage(tmp_path, [first, second], command)
    _write(tmp_path, "go.edit")
    run_coverage(tmp_path, [second], command)
    (tmp_path / "go.edit").unlink()
    run_coverage(tmp_path, [first], command)
    stamp = json.loads((tmp_path / STAMP).read_text(encoding="utf-8"))
    assert sorted(stamp["languages"]) == ["python"]
    assert stale_files(tmp_path, [first, second], command) == [second]


CACHES = [
    ".pytest_cache/v/cache/nodeids",
    "sub/.pytest_cache/v/x",
    ".hypothesis/examples/x",
    ".mypy_cache/3.12/x.json",
    ".ruff_cache/x",
    ".tox/py/log",
    ".nox/x",
    ".cpcache/123.cp",
    ".nyc_output/x.json",
    "htmlcov/index.html",
    ".coverage",
    "sub/.coverage.host.1.2",
]


def test_tool_caches_do_not_make_coverage_stale(tmp_path):
    source = _write(tmp_path, "src/a.py")
    assert _failed(tmp_path, [source], OK) == []
    for name in CACHES:
        _write(tmp_path, name)
    assert stale_files(tmp_path, [source], OK) == []


def test_tool_caches_written_by_the_coverage_run_still_stamp(tmp_path):
    source = _write(tmp_path, "src/a.py")
    command = chain(OK, *(_append(name) for name in CACHES))
    assert _failed(tmp_path, [source], command) == []
    assert stale_files(tmp_path, [source], command) == []
    assert _failed(tmp_path, [source], command) == []
    assert stale_files(tmp_path, [source], command) == []


def test_a_file_that_only_looks_like_a_cache_still_counts(tmp_path):
    source = _write(tmp_path, "src/a.py")
    run_coverage(tmp_path, [source], OK)
    _write(tmp_path, "src/coverage_util.py")
    assert stale_files(tmp_path, [source], OK) == [source]


def _typescript(root: Path, scripts: dict) -> list[Path]:
    _write(root, "package.json", json.dumps({"scripts": scripts}))
    (root / "node_modules/@vitest/coverage-v8").mkdir(parents=True, exist_ok=True)
    return [_write(root, "src/a.ts")]


def test_vitest_that_writes_no_lcov_fails_typescript(tmp_path, monkeypatch):
    files = _typescript(tmp_path, {"test": "vitest run"})
    monkeypatch.setattr("crapper.runners.run_shell", lambda command, cwd: 0)
    assert _failed(tmp_path, files) == ["typescript"]
    assert stale_files(tmp_path, files, None) == files
    monkeypatch.setattr("crapper.runners.run_shell", _succeed)
    assert _failed(tmp_path, files) == []
    assert (tmp_path / "target/coverage/typescript/lcov.info").is_file()


def test_a_coverage_script_is_trusted_by_its_exit_status(tmp_path, monkeypatch):
    files = _typescript(tmp_path, {"coverage": "anything"})
    monkeypatch.setattr("crapper.runners.run_shell", lambda command, cwd: 0)
    assert _failed(tmp_path, files) == []
