"""Coverage lifecycle acceptance tests through the real CLI process."""

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from crapper.runners import lua_interpreter


SOURCE = Path(__file__).resolve().parents[1] / "src"


def write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def cli(root, *args, env=None):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(SOURCE)
    if env:
        environment.update(env)
    return subprocess.run(
        [sys.executable, "-m", "crapper", "--root", str(root), *args],
        capture_output=True, text=True, env=environment,
    )


def shell_command(argv):
    # list2cmdline quotes for CreateProcess, not cmd.exe metacharacters.
    if os.name == "nt":
        return " ".join('"' + arg + '"' for arg in argv)
    return shlex.join(argv)


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("command", ["exit 9", "crapper_nonexistent_coverage_executable_5"])
def test_failed_custom_command_stops_publication(tmp_path, existing, command):
    project = tmp_path / "project with spaces"
    write(project, "src/calc.lua", "local M = {}; function M.value() return 42 end; return M\n")
    snapshot = project / ".metrics" / "crap.edn"
    previous = b'{:entries []}\r\n; previous snapshot\r\n'
    if existing:
        snapshot.parent.mkdir()
        snapshot.write_bytes(previous)
    result = cli(project, "--coverage-command", command)
    assert result.returncode == 1, result.stderr
    assert "Coverage command exited" in result.stderr
    assert "Wrote" not in result.stderr
    assert snapshot.read_bytes() == previous if existing else not snapshot.exists()


@pytest.mark.skipif(lua_interpreter() is None, reason="Lua 5.4 is not installed")
def test_failed_lua_tests_stop_conversion_and_publication(tmp_path):
    project = tmp_path / "lua project with spaces"
    write(project, ".busted", "return {default = {lpath = 'src/?.lua'}}")
    write(project, "src/calc.lua", "local M = {}; function M.value() return 42 end; return M\n")
    write(project, "spec/calc_spec.lua",
          "local calc = require('calc')\n"
          "describe('calc', function() it('fails', function() "
          "assert.equals(0, calc.value()) end) end)\n")
    snapshot = write(project, ".metrics/crap.edn", "previous snapshot\n")
    previous = snapshot.read_bytes()
    result = cli(project)
    assert result.returncode == 1, result.stderr
    assert "Lua tests exited 1" in result.stderr
    assert "c.reporter = 'lcov'" not in result.stderr
    assert not (project / "target/coverage/lua/lcov.info").exists()
    assert snapshot.read_bytes() == previous


@pytest.mark.skipif(lua_interpreter() is None, reason="Lua 5.4 is not installed")
def test_missing_luacov_stats_stop_publication(tmp_path):
    project = tmp_path / "lua project with spaces"
    write(project, ".busted", "return {default = {lpath = 'src/?.lua'}}")
    write(project, "src/calc.lua", "local M = {}; function M.value() return 42 end; return M\n")
    write(project, "spec/calc_spec.lua",
          "local runner = require('luacov.runner')\n"
          "runner.shutdown()\n"
          "os.remove(runner.configuration.statsfile)\n"
          "os.exit(0)\n")
    result = cli(project)
    assert result.returncode == 1, result.stderr
    assert "LuaCov stats missing" in result.stderr
    assert not (project / ".metrics/crap.edn").exists()


@pytest.mark.skipif(lua_interpreter() is None, reason="Lua 5.4 is not installed")
@pytest.mark.parametrize("reporter, diagnostic", [
    ("error('reporter failure for acceptance test')\n", "LuaCov LCOV conversion exited"),
    ("return {report = function() end}\n", "LuaCov LCOV report missing"),
])
def test_luacov_conversion_failure_preserves_snapshot(tmp_path, reporter, diagnostic):
    project = tmp_path / "lua project with spaces"
    write(project, ".busted", "return {default = {lpath = 'src/?.lua'}}")
    write(project, "src/calc.lua", "local M = {}; function M.value() return 42 end; return M\n")
    write(project, "spec/calc_spec.lua",
          "local calc = require('calc')\n"
          "describe('calc', function() it('passes', function() "
          "assert.equals(42, calc.value()) end) end)\n")
    write(project, "luacov/reporter/lcov.lua", reporter)
    snapshot = write(project, ".metrics/crap.edn", "previous snapshot\n")
    result = cli(project)
    assert result.returncode == 1, result.stderr
    assert diagnostic in result.stderr
    assert snapshot.read_text(encoding="utf-8") == "previous snapshot\n"


def test_custom_command_keeps_shell_syntax_arguments_and_root_cwd(tmp_path):
    project = tmp_path / "project with spaces"
    write(project, "src/calc.lua", "local M = {}; function M.value() return 42 end; return M\n")
    script = write(tmp_path, "script directory/coverage command.py",
                   "import os, sys\nfrom pathlib import Path\n"
                   "assert sys.argv[1:] == ['argument with spaces', 'a&b', \"quote'word\"]\n"
                   f"assert Path.cwd() == Path({str(project)!r})\n"
                   "report = Path('coverage/lcov.info')\nreport.parent.mkdir(exist_ok=True)\n"
                   "report.write_text('SF:src/calc.lua\\nDA:1,1\\nLF:1\\nLH:1\\nend_of_record\\n')\n")
    command = shell_command([sys.executable, str(script), "argument with spaces", "a&b", "quote'word"])
    result = cli(project, "--coverage-command", command + ' && echo shell-success > "shell marker.txt"')
    assert result.returncode == 0, result.stderr
    assert (project / "shell marker.txt").read_text().strip() == "shell-success"
    assert ":coverage 100.0" in (project / ".metrics/crap.edn").read_text()


@pytest.mark.parametrize("existing", [False, True])
def test_success_without_fresh_custom_report_stops_publication(tmp_path, existing):
    project = tmp_path / "project with spaces"
    write(project, "src/calc.lua", "local M = {}; function M.value() return 42 end; return M\n")
    write(project, "coverage/lcov.info", "SF:src/calc.lua\nDA:1,1\nLF:1\nLH:1\nend_of_record\n")
    write(project, "target/coverage/alternate/lcov.info", "SF:src/calc.lua\nDA:1,1\nend_of_record\n")
    snapshot = project / ".metrics/crap.edn"
    if existing:
        write(project, ".metrics/crap.edn", "old snapshot\n")
    result = cli(project, "--coverage-command", "echo success")
    assert result.returncode == 1, result.stderr
    assert snapshot.read_text() == "old snapshot\n" if existing else not snapshot.exists()
    assert not (project / "coverage/lcov.info").exists()


@pytest.mark.parametrize("record", [
    "", " \n", "garbage\n", "SF:src/calc.lua\nDA:1,1\nLF:1\nLH:1\n",
    "SF:src/calc.lua\nDA:1,1\nend_of_record\n",
    "SF:src/calc.lua\nDA:1,-1\nLF:1\nLH:0\nend_of_record\n",
    "SF:src/calc.lua\nDA:1,1,junk\nLF:1\nLH:1\nend_of_record\n",
    "SF:src/calc.lua\nDA:1,1\nDA:1,0\nLF:1\nLH:1\nend_of_record\n",
    "SF:src/calc.lua\nDA:1,1\nLF:2\nLH:1\nend_of_record\n",
    "SF:src/calc.lua\nDA:1,1\nLF:1\nLH:0\nend_of_record\n",
    "SF:src/missing.lua\nDA:1,1\nLF:1\nLH:1\nend_of_record\n",
    "SF:src\nDA:1,1\nLF:1\nLH:1\nend_of_record\n",
    "SF:src/calc.lua\nSF:src/calc.lua\nLF:0\nLH:0\nend_of_record\n",
    "DA:1,1\nSF:src/calc.lua\nLF:0\nLH:0\nend_of_record\n",
    "SF:src/calc.lua\nLF:0\nLF:0\nLH:0\nend_of_record\n",
    "SF:src/calc.lua\nDA:0,1\nLF:1\nLH:1\nend_of_record\n",
])
@pytest.mark.parametrize("existing", [False, True])
def test_malformed_fresh_report_stops_publication(tmp_path, record, existing):
    project = tmp_path / "project with spaces"
    write(project, "src/calc.lua", "local M = {}; function M.value() return 42 end; return M\n")
    script = write(tmp_path, "writer.py", "from pathlib import Path\n"
                   "p = Path('coverage/lcov.info'); p.parent.mkdir(exist_ok=True)\n"
                   f"p.write_text({record!r}, encoding='utf-8')\n")
    previous = None
    if existing:
        previous = write(project, ".metrics/crap.edn", "previous snapshot\n").read_bytes()
    result = cli(project, "--coverage-command", shell_command([sys.executable, str(script)]))
    assert result.returncode == 1, result.stderr
    snapshot = project / ".metrics/crap.edn"
    assert snapshot.read_bytes() == previous if existing else not snapshot.exists()


@pytest.mark.parametrize("failing", [False, True])
def test_custom_artifacts_invalidated_before_execution_without_contamination(tmp_path, failing):
    project = tmp_path / "project with spaces"
    write(project, "src/calc.lua", "local M = {}; function M.value() return 42 end; return M\n")
    managed = ["coverage/lcov.info", "coverage/luacov.stats.out", "luacov.stats.out"]
    for path in managed:
        write(project, path, "old")
    unrelated = write(project, "coverage/caller.stats", "keep")
    write(project, "target/coverage/alternate/lcov.info", "SF:src/calc.lua\nDA:1,1\nend_of_record\n")
    write(project, "target/coverage/src/calc.lua.html", '<span title="1 out of 1 forms covered">1&nbsp;</span>')
    script = write(tmp_path, "managed writer.py", "from pathlib import Path\n"
                   f"assert all(not Path(p).exists() for p in {managed!r})\n"
                   "assert Path('coverage/caller.stats').read_text() == 'keep'\n"
                   + ("raise SystemExit(9)\n" if failing else
                      "Path('coverage/lcov.info').write_text('SF:src/calc.lua\\nDA:1,0\\nLH:0\\nLF:1\\nend_of_record\\n')\n"))
    result = cli(project, "--coverage-command", shell_command([sys.executable, str(script)]))
    assert result.returncode == (1 if failing else 0), result.stderr
    assert unrelated.read_text() == "keep"
    if failing:
        assert all(not (project / p).exists() for p in managed)
        assert not (project / ".metrics/crap.edn").exists()
    else:
        assert ":coverage 0.0" in (project / ".metrics/crap.edn").read_text()


@pytest.mark.parametrize("source_kind", ["forward", "backslash", "absolute"])
def test_valid_custom_line_records_support_reporter_metadata_and_source_paths(tmp_path, source_kind):
    project = tmp_path / "project with spaces"
    source = write(project, "src/calc.lua", "local M = {}; function M.value() return 42 end; return M\n")
    write(project, "src/empty.lua", "-- no executable lines\n")
    sf = {"forward": "src/calc.lua", "backslash": "src\\calc.lua", "absolute": str(source)}[source_kind]
    report = (f"TN:tests\r\nSF:{sf}\r\nDA:1,3,{'a' * 32}\r\nLH:1\r\nLF:1\r\nend_of_record\r\n"
              "SF:src/empty.lua\r\nLH:0\r\nLF:0\r\nend_of_record")
    script = write(tmp_path, "valid writer.py", "from pathlib import Path\n"
                   "p = Path('coverage/lcov.info'); p.parent.mkdir(exist_ok=True)\n"
                   f"p.write_bytes({report.encode()!r})\n")
    result = cli(project, "--coverage-command", shell_command([sys.executable, str(script)]), "--threshold", "0")
    assert result.returncode == 2, result.stderr
    assert ":coverage 100.0" in (project / ".metrics/crap.edn").read_text()


@pytest.mark.skipif(lua_interpreter() is None, reason="Lua 5.4 is not installed")
def test_standard_busted_lcov_cli_keeps_untested_sources(tmp_path):
    import shutil
    from crapper.coverage import read_fresh_lcov
    fixture = Path(__file__).parent / "fixtures/lua_project"
    project = tmp_path / "standard Lua project with spaces"
    shutil.copytree(fixture, project)
    result = cli(project)
    assert result.returncode == 0, result.stderr
    report = project / "target/coverage/lua/lcov.info"
    data = read_fresh_lcov(report, project)
    assert str((project / "src/calc/util.lua").resolve()) in data
    snapshot = (project / ".metrics/crap.edn").read_text()
    assert '{:name "M.untested", :namespace "calc.util", :complexity 2, :coverage 0.0' in snapshot
    assert ":coverage 0.0" in snapshot


@pytest.mark.skipif(lua_interpreter() is None, reason="Lua 5.4 is not installed")
def test_all_lua_module_artifacts_are_invalid_before_first_busted_run(tmp_path):
    project = tmp_path / "multi module project with spaces"
    artifacts = ["target/coverage/lua/lcov.info", "target/coverage/lua/luacov.stats.out",
                 "target/coverage/lua/child/lcov.info", "target/coverage/lua/child/luacov.stats.out"]
    write(project, ".busted", "return {default = {lpath = 'src/?.lua'}}")
    write(project, "child/.busted", "return {}")
    write(project, "src/calc.lua", "local M = {}; function M.value() return 42 end; return M\n")
    write(project, "child/src/calc.lua", "return {}\n")
    checks = "\n".join(f"assert(io.open('{path}', 'r') == nil)" for path in artifacts)
    write(project, "spec/check_spec.lua", checks + "\nerror('deliberate failure after invalidation checks')\n")
    for path in artifacts:
        write(project, path, "old")
    result = cli(project)
    assert result.returncode == 1, result.stderr
    assert "deliberate failure after invalidation checks" in result.stdout + result.stderr
    assert not (project / "target/coverage/lua/child/luacov.stats.out").exists()
    assert not (project / "target/coverage/lua/child/lcov.info").exists()
    assert not (project / ".metrics/crap.edn").exists()


def test_metric_threshold_exit_publishes_valid_snapshot(tmp_path):
    project = tmp_path / "project with spaces"
    write(project, "src/calc.lua", "local M = {}; function M.value() return 42 end; return M\n")
    write(project, "coverage/lcov.info", "SF:src/calc.lua\nDA:1,1\nend_of_record\n")
    result = cli(project, "--use-existing-coverage", "--threshold", "0")
    assert result.returncode == 2, result.stderr
    assert "CRAP threshold exceeded" in result.stderr
    assert ":coverage 100.0" in (project / ".metrics/crap.edn").read_text()
