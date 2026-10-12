import os
import shutil
from pathlib import Path

import pytest

from crapper.analyze import analyze_files
from crapper.coverage import load_bundle
from crapper.discover import iter_source_files
from crapper.runners import (
    CoverageError,
    _clean_clojure,
    absolute_lcov_sources,
    lua_coverage_commands,
    lua_coverage_config,
    lua_interpreter,
    lua_roots,
    run_coverage,
    run_shell,
)

FIXTURE = Path(__file__).parent / "fixtures" / "lua_project"


def _write(root: Path, relative: str, text: str = "") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_lua_root_is_the_nearest_busted_or_rockspec_directory(tmp_path):
    _write(tmp_path, "a/.busted")
    _write(tmp_path, "b/demo-1.0-1.rockspec")
    files = [
        _write(tmp_path, "a/src/x.lua"),
        _write(tmp_path, "b/y.lua"),
        _write(tmp_path, "c/z.lua"),
        _write(tmp_path, "c/z.py"),
    ]
    assert lua_roots(tmp_path, files) == [
        tmp_path.resolve(),
        (tmp_path / "a").resolve(),
        (tmp_path / "b").resolve(),
    ]


def test_lua_interpreter_prefers_lua54_and_checks_the_version(monkeypatch):
    paths = {"lua5.4": "/opt/lua5.4", "lua": "/opt/lua"}
    versions = {"/opt/lua5.4": "Lua 5.5", "/opt/lua": "Lua 5.4"}
    monkeypatch.setattr("crapper.runners.shutil.which", paths.get)
    monkeypatch.setattr("crapper.runners._lua_version", versions.get)
    assert lua_interpreter() == str(Path("/opt/lua").resolve())


def test_lua_interpreter_none_when_no_lua54(monkeypatch):
    monkeypatch.setattr("crapper.runners.shutil.which", lambda name: "/opt/lua")
    monkeypatch.setattr("crapper.runners._lua_version", lambda binary: "Lua 5.1")
    assert lua_interpreter() is None


def test_missing_lua_stops_coverage(tmp_path, monkeypatch):
    monkeypatch.setattr("crapper.runners.lua_interpreter", lambda: None)
    with pytest.raises(CoverageError, match="No Lua 5.4 interpreter"):
        run_coverage(tmp_path, [_write(tmp_path, "a.lua")], None)
    assert not (tmp_path / "target").exists()


def test_lua_coverage_config_and_commands(tmp_path):
    stats = tmp_path / "out" / "luacov.stats.out"
    report = tmp_path / "out" / "lcov.info"
    config = lua_coverage_config(stats, report)
    assert f"statsfile = [==[{stats}]==]" in config
    assert f"reportfile = [==[{report}]==]" in config
    assert "includeuntestedfiles = true" in config
    assert "[==[_spec$]==]" in config
    run, lcov = lua_coverage_commands("/opt/lua 5.4/bin/lua", tmp_path / "cfg.lua")
    assert run[0] == lcov[0] == "/opt/lua 5.4/bin/lua"
    assert run[-4:] == ["busted", "--ignore-lua", "-c", f"--coverage-config-file={tmp_path / 'cfg.lua'}"]
    assert lcov[-2:] == ["luacov", str(tmp_path / "cfg.lua")]


def test_relative_lcov_sources_are_anchored_to_the_lua_root(tmp_path):
    report = _write(tmp_path, "lcov.info", "SF:src/a.lua\nDA:1,1\nSF:/abs/b.lua\n")
    absolute_lcov_sources(report, Path("/proj/pkg"))
    assert report.read_text(encoding="utf-8") == "SF:/proj/pkg/src/a.lua\nDA:1,1\nSF:/abs/b.lua\n"


def test_clean_clojure_keeps_lua_reports(tmp_path):
    kept = _write(tmp_path, "target/coverage/lua/lcov.info", "keep")
    _clean_clojure(tmp_path)
    assert kept.is_file()


def _toolchain_ready() -> bool:
    return all(shutil.which(tool) for tool in ("busted", "luacov")) and lua_interpreter() is not None


@pytest.mark.skipif(not _toolchain_ready(), reason="busted, luacov, and Lua 5.4 are not installed")
def test_lua_executable_reads_config_paths_with_spaces(tmp_path):
    binary = os.environ.get("CRAPPER_TEST_LUA") or lua_interpreter()
    if binary is None or Path(binary).suffix.lower() in (".cmd", ".bat"):
        pytest.skip("set CRAPPER_TEST_LUA to a real Lua 5.4 executable to test native argv")
    # Copy the runtime alongside the executable (Windows Lua may need its DLL).
    runtime = tmp_path / "lua runtime with spaces"
    runtime.mkdir()
    shutil.copy2(binary, runtime)
    for dll in Path(binary).parent.glob("*.dll"):
        shutil.copy2(dll, runtime)
    executable = runtime / Path(binary).name
    config = _write(tmp_path, "config directory/luacov.cfg.lua",
                    lua_coverage_config(tmp_path / "stats file", tmp_path / "report file"))
    assert run_shell([str(executable), "-e",
                      "local c = dofile(arg[0]); assert(c.includeuntestedfiles == true)",
                      "--", str(config)], tmp_path) == 0


@pytest.mark.skipif(not _toolchain_ready(), reason="busted, luacov, and Lua 5.4 are not installed")
@pytest.mark.parametrize("failing", [False, True])
def test_busted_bootstrap_exits_after_running_tests(tmp_path, capfd, failing):
    project = tmp_path / "lua project with spaces"
    _write(project, ".busted", "return {default = {lpath = 'src/?.lua'}}")
    _write(project, "src/calc.lua", "return {value = function() return 42 end}\n")
    expected = 0 if failing else 42
    _write(project, "spec/calc_spec.lua",
           "local calc = require('calc')\n"
           "describe('calc', function()\n"
           "  it('passes', function() assert.equals(42, calc.value()) end)\n"
           f"  it('checks value', function() assert.equals({expected}, calc.value()) end)\n"
           "end)\n")
    stats = project / "stats file"
    report = project / "report file"
    config = _write(project, "config directory/luacov.cfg.lua",
                    lua_coverage_config(stats, report))
    run, lcov = lua_coverage_commands(lua_interpreter(), config)
    code = run_shell(run, project)
    output = capfd.readouterr()
    assert "cannot open busted" not in output.err
    assert ("1 success / 1 failure" if failing else "2 successes / 0 failures") in output.out
    assert code == (1 if failing else 0)
    assert stats.is_file()
    assert run_shell(lcov, project) == 0
    text = report.read_text(encoding="utf-8")
    source_record = next(record for record in text.replace("\\", "/").split("end_of_record")
                         if "SF:src/calc.lua\n" in record)
    assert "LH:1\n" in source_record


@pytest.mark.skipif(not _toolchain_ready(), reason="busted, luacov, and Lua 5.4 are not installed")
@pytest.mark.parametrize("runtime_with_spaces", [False, True])
def test_fixture_project_end_to_end(tmp_path, monkeypatch, runtime_with_spaces):
    if runtime_with_spaces:
        binary = Path(lua_interpreter())
        runtime = tmp_path / "lua runtime with spaces"
        runtime.mkdir()
        shutil.copy2(binary, runtime)
        for dll in binary.parent.glob("*.dll"):
            shutil.copy2(dll, runtime)
        executable = str(runtime / binary.name)
        monkeypatch.setattr("crapper.runners.lua_interpreter", lambda: executable)
    project = tmp_path / "lua project with spaces"
    shutil.copytree(FIXTURE, project)
    if runtime_with_spaces:
        _write(project, ".busted", "return {default = {lpath = 'src/?.lua;src/?/init.lua', lua = 'missing-lua'}}")
    monkeypatch.chdir(project)
    files = iter_source_files([project])
    run_coverage(project, files, None)
    entries = analyze_files(files, project, load_bundle(project))
    assert [(e.namespace, e.name, e.complexity, e.coverage) for e in entries] == [
        ("calc.util", "M.untested", 2, 0.0),
        ("calc.util", "M.clamp", 4, 100.0),
        ("calc", "M.classify", 3, pytest.approx(83.3333, abs=1e-3)),
        ("calc", "M.total", 2, 100.0),
        ("calc.util", "between", 2, 100.0),
    ]
