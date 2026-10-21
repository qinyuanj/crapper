"""The Lua golden fixture through the real CLI: snapshot bytes must not move."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from crapper.runners import lua_cluacov_version, lua_interpreter

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = FIXTURES / "lua_golden"


def project(tmp_path):
    root = tmp_path / "lua golden project"
    shutil.copytree(GOLDEN, root)
    return root


def cli(root, *args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "crapper", "--root", str(root), *args],
        capture_output=True, text=True, check=False, env={**os.environ, **(env or {})},
    )


def published(root):
    return (root / ".metrics" / "crap.edn").read_bytes()


@pytest.mark.parametrize("parallel", [None, "1"])
def test_complexity_snapshot_matches_golden(tmp_path, parallel):
    root = project(tmp_path)
    env = {"CRAPPER_PARALLEL_MIN_FILES": parallel} if parallel else None
    result = cli(root, "--full-tree", "--no-coverage", env=env)
    assert result.returncode == 0, result.stderr
    assert published(root) == (FIXTURES / "lua_golden.no-coverage.edn").read_bytes()


def _toolchain_ready() -> bool:
    lua = lua_interpreter()
    return lua is not None and lua_cluacov_version(lua) == "1.0.0"


@pytest.mark.skipif(not _toolchain_ready(), reason="Lua 5.4 with cluacov 1.0.0 is not installed")
def test_sharded_single_and_existing_coverage_match_golden(tmp_path):
    golden = (FIXTURES / "lua_golden.coverage.edn").read_bytes()
    root = project(tmp_path)
    result = cli(root, "--full-tree")
    assert result.returncode == 0, result.stderr
    assert published(root) == golden
    report = (root / "target" / "coverage" / "lua" / "lcov.info").read_text(encoding="utf-8")
    sources = [line[3:] for line in report.splitlines() if line.startswith("SF:")]
    assert sorted(Path(path).relative_to(root.resolve()).as_posix() for path in sources) == [
        "src/lua/deep/thing.lua", "src/shapes/init.lua",
        "src/shapes/modern.lua", "src/shapes/untested.lua",
    ]

    result = cli(root, "--full-tree", "--lua-shards", "1")
    assert result.returncode == 0, result.stderr
    assert published(root) == golden

    result = cli(root, "--use-existing-coverage")
    assert result.returncode == 0, result.stderr
    assert published(root) == golden
