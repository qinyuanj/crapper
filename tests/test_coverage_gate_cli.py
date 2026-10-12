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
                   "report = Path('coverage/lcov.info')\nreport.parent.mkdir()\n"
                   "report.write_text('SF:src/calc.lua\\nDA:1,1\\nend_of_record\\n')\n")
    command = shell_command([sys.executable, str(script), "argument with spaces", "a&b", "quote'word"])
    result = cli(project, "--coverage-command", command + ' && echo shell-success > "shell marker.txt"')
    assert result.returncode == 0, result.stderr
    assert (project / "shell marker.txt").read_text().strip() == "shell-success"
    assert ":coverage 100.0" in (project / ".metrics/crap.edn").read_text()


def test_metric_threshold_exit_publishes_valid_snapshot(tmp_path):
    project = tmp_path / "project with spaces"
    write(project, "src/calc.lua", "local M = {}; function M.value() return 42 end; return M\n")
    write(project, "coverage/lcov.info", "SF:src/calc.lua\nDA:1,1\nend_of_record\n")
    result = cli(project, "--use-existing-coverage", "--threshold", "0")
    assert result.returncode == 2, result.stderr
    assert "CRAP threshold exceeded" in result.stderr
    assert ":coverage 100.0" in (project / ".metrics/crap.edn").read_text()
