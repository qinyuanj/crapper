"""Complete-tree selection and publication through the real CLI boundary."""

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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file sharing semantics")
@pytest.mark.parametrize("locked", ["snapshot", "source"])
def test_native_windows_locked_file_preserves_snapshot_and_cleans_staging(tmp_path, locked):
    import ctypes
    from ctypes import wintypes

    root = tmp_path / "business project"
    source(root, "client/main.lua", "main")
    destination = snapshot(root)
    destination.parent.mkdir()
    original = b"previous snapshot\x00\xff\r\n"
    destination.write_bytes(original)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    locked_path = destination if locked == "snapshot" else root / "client/main.lua"
    share = 1 if locked == "snapshot" else 0
    handle = kernel32.CreateFileW(str(locked_path), 0x80000000, share, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        result = cli(root, "--full-tree", "--no-coverage")
        assert result.returncode == 1
        diagnostic = "Snapshot publication failed" if locked == "snapshot" else "Report rendering failed"
        assert diagnostic in result.stderr
        assert "Wrote " not in result.stderr
        assert destination.read_bytes() == original
        assert list(destination.parent.iterdir()) == [destination]
    finally:
        kernel32.CloseHandle(handle)


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
