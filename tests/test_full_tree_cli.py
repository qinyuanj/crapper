"""Complete-tree selection and publication through the real CLI boundary."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def source(root, relative, name):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"function {name}() return 1 end\n", encoding="utf-8")
    return path


def cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "crapper", "--root", str(root), *args],
        capture_output=True, text=True, check=False,
    )


def snapshot(root):
    return root / ".metrics" / "crap.edn"


def test_complete_configured_tree_respects_exclusions_and_replaces_deleted_entries(tmp_path):
    root = tmp_path / "business project"
    deleted = source(root, "client/main.lua", "client_main")
    source(root, "common/main.lua", "shared")
    source(root, "server/main.lua", "server_main")
    source(root, "server/_trigger/event.lua", "excluded_trigger")
    source(root, "packages/vendor.lua", "excluded_package")
    source(root, "client/_trigger/event.lua", "client_trigger")
    args = ["--full-tree", "--no-coverage", "--source-root", "client",
            "--source-root", "common", "--source-root", str(root / "server"),
            "--source-root", "packages", "--exclude", "packages",
            "--exclude", "server\\_trigger"]
    result = cli(root, *args)
    assert result.returncode == 0, result.stderr
    text = snapshot(root).read_text(encoding="utf-8")
    for name in ("client_main", "shared", "server_main", "client_trigger"):
        assert text.count(f':name "{name}"') == 1
        assert name in result.stdout
    assert "excluded_" not in text
    assert ":coverage nil" in text
    deleted.unlink()
    result = cli(root, *args)
    assert result.returncode == 0, result.stderr
    assert "client_main" not in snapshot(root).read_text(encoding="utf-8")


def test_deleting_final_source_publishes_explicit_empty_snapshot(tmp_path):
    path = source(tmp_path, "client/main.lua", "last_source")
    args = ["--full-tree", "--no-coverage", "--source-root", "client"]
    assert cli(tmp_path, *args).returncode == 0
    path.unlink()
    result = cli(tmp_path, *args)
    assert result.returncode == 0, result.stderr
    assert snapshot(tmp_path).read_text(encoding="utf-8").strip() == "{:entries []}"
    assert "empty" in result.stderr.lower()
    assert "no coverage" in result.stderr.lower()


@pytest.mark.parametrize("narrowing", [["--changed"], ["client"], ["main"],
                                       ["client/main.lua"], ["--use-existing-coverage"]])
def test_full_tree_rejects_partial_or_existing_coverage_selection(tmp_path, narrowing):
    source(tmp_path, "client/main.lua", "main")
    snapshot(tmp_path).parent.mkdir()
    snapshot(tmp_path).write_bytes(b"previous snapshot")
    result = cli(tmp_path, "--full-tree", "--no-coverage", *narrowing)
    assert result.returncode == 1
    assert "--full-tree cannot" in result.stderr
    assert snapshot(tmp_path).read_bytes() == b"previous snapshot"


@pytest.mark.parametrize("root_arg", ["missing", "../outside"])
def test_invalid_full_tree_roots_preserve_snapshot(tmp_path, root_arg):
    source(tmp_path, "client/main.lua", "main")
    (tmp_path.parent / "outside").mkdir(exist_ok=True)
    snapshot(tmp_path).parent.mkdir()
    snapshot(tmp_path).write_bytes(b"previous snapshot")
    result = cli(tmp_path, "--full-tree", "--no-coverage", "--source-root", root_arg)
    assert result.returncode == 1
    assert "Source selection failed" in result.stderr
    assert snapshot(tmp_path).read_bytes() == b"previous snapshot"


@pytest.mark.parametrize("boundary", ["staging", "replacement"])
def test_publication_filesystem_failures_are_diagnosed_without_partial_destination(tmp_path, boundary):
    source(tmp_path, "client/main.lua", "main")
    if boundary == "staging":
        blocker = tmp_path / ".metrics"
        blocker.write_bytes(b"filesystem blocker")
    else:
        blocker = snapshot(tmp_path)
        blocker.mkdir(parents=True)
        (blocker / "previous").write_bytes(b"previous snapshot")
    result = cli(tmp_path, "--full-tree", "--no-coverage")
    assert result.returncode == 1
    assert "Snapshot publication failed" in result.stderr
    assert "Traceback" not in result.stderr
    if boundary == "staging":
        assert blocker.read_bytes() == b"filesystem blocker"
    else:
        assert (blocker / "previous").read_bytes() == b"previous snapshot"
        assert list(blocker.parent.iterdir()) == [blocker]


@pytest.mark.parametrize("selection", [["client/main.lua"], ["main"], ["--changed"]])
def test_legacy_narrowing_still_selects_partial_snapshot(tmp_path, selection):
    source(tmp_path, "client/main.lua", "main")
    source(tmp_path, "common/other.lua", "other")
    if selection == ["--changed"]:
        subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(tmp_path), "add", "client/main.lua"],
                       check=True, capture_output=True)
        # The other source is ignored by Git, so it is absent from changed selection.
        (tmp_path / ".gitignore").write_text("common/\n", encoding="utf-8")
    result = cli(tmp_path, "--no-coverage", "--use-existing-coverage", *selection)
    assert result.returncode == 0, result.stderr
    text = snapshot(tmp_path).read_text(encoding="utf-8")
    assert ':name "main"' in text
    assert ':name "other"' not in text


def write_text(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.skipif(not all(shutil.which(tool) for tool in ("lua", "busted", "luacov")),
                    reason="native Lua coverage toolchain unavailable")
def test_full_tree_fresh_standard_reporter_refresh_and_failures(tmp_path):
    root = tmp_path / "covered business project"
    for directory, name in (("client", "client_main"), ("common", "shared"), ("server", "server_main")):
        source(root, f"{directory}/main.lua", name)
    source(root, "packages/vendor.lua", "excluded_package")
    source(root, "server/_trigger/event.lua", "excluded_trigger")
    source(root, "client/spec/helper.lua", "excluded_spec")
    write_text(root, ".busted", "return {default = {lpath = './?.lua'}}\n")
    spec = write_text(root, "spec/business_spec.lua",
                     "require('client.main'); require('common.main'); require('server.main')\n"
                     "describe('business', function()\n"
                     "it('covers all roots', function()\n"
                     "assert.equals(1, client_main()); assert.equals(1, shared()); "
                     "assert.equals(1, server_main())\nend)\nend)\n")
    old_report = "SF:client/main.lua\nDA:1,0\nLF:1\nLH:0\nend_of_record\n"
    managed = root / "target/coverage/lua"
    write_text(root, "target/coverage/lua/lcov.info", old_report)
    write_text(root, "target/coverage/lua/luacov.stats.out", "invalid stale stats")
    alternate = write_text(root, "coverage/lcov.info", old_report)
    sentinel = write_text(root, "target/coverage/lua/caller.txt", "caller-owned")
    args = ["--full-tree", "--source-root", "client", "--source-root", "common",
            "--source-root", "server", "--source-root", "packages",
            "--exclude", "packages", "--exclude", "server/_trigger"]
    result = cli(root, *args)
    assert result.returncode == 0, result.stderr
    text = snapshot(root).read_text(encoding="utf-8")
    assert text.count(":name ") == 3
    assert text.count(":coverage 100.0") == 3
    assert text.count(":crap 1.0") == 3
    assert "CRAP Report" in result.stdout
    assert "excluded_" not in text
    assert (managed / "lcov.info").read_text(encoding="utf-8") != old_report
    assert alternate.read_text(encoding="utf-8") == old_report
    assert sentinel.read_text(encoding="utf-8") == "caller-owned"

    # Exit 2 follows a valid replacement, rather than preserving the prior snapshot.
    snapshot(root).write_bytes(b"previous threshold snapshot")
    result = cli(root, *args, "--threshold", "0")
    assert result.returncode == 2, result.stderr
    assert snapshot(root).read_text(encoding="utf-8") == text

    # A covered refresh removes the deleted source while retaining the other roots.
    (root / "client/main.lua").unlink()
    spec.write_text("require('common.main'); require('server.main')\n"
                    "describe('remaining', function() it('passes', function() "
                    "assert.equals(1, shared()); assert.equals(1, server_main()) end) end)\n",
                    encoding="utf-8")
    result = cli(root, *args)
    assert result.returncode == 0, result.stderr
    refreshed = snapshot(root).read_text(encoding="utf-8")
    assert "client_main" not in refreshed
    assert refreshed.count(":coverage 100.0") == 2

    # A real failing Busted run invalidates managed LCOV without publishing.
    spec.write_text("describe('failure', function() it('fails', function() assert.equals(1, 2) end) end)\n",
                    encoding="utf-8")
    original = snapshot(root).read_bytes()
    result = cli(root, *args)
    assert result.returncode == 1
    assert "Coverage failed" in result.stderr
    assert snapshot(root).read_bytes() == original
    assert not (managed / "lcov.info").exists()
    assert sentinel.read_text(encoding="utf-8") == "caller-owned"

    # Deleting the final business sources yields an explicit empty snapshot.
    for directory in ("common", "server"):
        (root / directory / "main.lua").unlink()
    result = cli(root, *args)
    assert result.returncode == 0, result.stderr
    assert snapshot(root).read_bytes() == b"{:entries []}\n"
    assert "no coverage ran" in result.stderr.lower()


@pytest.mark.parametrize("empty", [False, True])
def test_explicit_failing_custom_command_invalidates_artifacts_and_preserves_snapshot(tmp_path, empty):
    source(tmp_path, "client/main.lua", "main")
    if empty:
        (tmp_path / "client/main.lua").unlink()
    snapshot(tmp_path).parent.mkdir()
    original = b"previous snapshot\x00\xff\r\n"
    snapshot(tmp_path).write_bytes(original)
    artifacts = [write_text(tmp_path, path, "stale") for path in
                 ("coverage/lcov.info", "coverage/luacov.stats.out", "luacov.stats.out")]
    command = f'"{sys.executable}" -c "raise SystemExit(7)"'
    result = cli(tmp_path, "--full-tree", "--source-root", "client", "--coverage-command", command)
    assert result.returncode == 1
    assert "Coverage failed" in result.stderr
    assert "after custom coverage validation" not in result.stderr
    assert snapshot(tmp_path).read_bytes() == original
    assert all(not path.exists() for path in artifacts)


def test_empty_tree_custom_command_requires_fresh_valid_report_before_publication(tmp_path):
    (tmp_path / "client").mkdir()
    snapshot(tmp_path).parent.mkdir()
    snapshot(tmp_path).write_bytes(b"previous snapshot")
    command = f'"{sys.executable}" -c "pass"'
    args = ["--full-tree", "--source-root", "client", "--coverage-command"]
    result = cli(tmp_path, *args, command)
    assert result.returncode == 1
    assert snapshot(tmp_path).read_bytes() == b"previous snapshot"
    write_text(tmp_path, "excluded.lua", "-- no executable lines\n")
    script = write_text(tmp_path, "emit_report.py",
                        "from pathlib import Path\n"
                        "p = Path('coverage/lcov.info'); p.parent.mkdir(exist_ok=True)\n"
                        "p.write_text('SF:excluded.lua\\nLF:0\\nLH:0\\nend_of_record\\n')\n")
    result = cli(tmp_path, *args, f'"{sys.executable}" "{script}"')
    assert result.returncode == 0, result.stderr
    assert snapshot(tmp_path).read_bytes() == b"{:entries []}\n"
    assert "after custom coverage validation" in result.stderr
