import sys
from pathlib import Path
from types import SimpleNamespace

import crapper.platform
from crapper.cli import platform_warning, run as run_cli
from crapper.platform import run


def test_any_windows_build_runs(tmp_path, monkeypatch):
    """A Windows host is decided by its OS, not by a build number; 19042 is Windows 10 20H2.

    Both halves of the decision are patched: `sys.platform`, which the CLI reads,
    and `crapper.platform.WINDOWS`, which is bound from it at import. Patching the
    build alone would leave the body on the Linux path of a Linux runner, where no
    Windows refusal fires and a reintroduced build floor would still pass.
    """

    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "demo.py").write_text("def choose(x):\n    return 1 if x else 0\n", encoding="utf-8")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(crapper.platform, "WINDOWS", True)
    monkeypatch.setattr(sys, "getwindowsversion", lambda: SimpleNamespace(build=19042), raising=False)

    assert crapper.platform.WINDOWS, "the Windows decision has to be the one this test exercises"

    assert run_cli(["--root", str(project), "--no-coverage"]) == 0


def test_native_windows_paths_are_quiet():
    assert platform_warning("win32", Path("C:/proj")) is None


def test_windows_drive_under_wsl_suggests_the_linux_filesystem():
    """The WSL2 warning, specified though no delivery run reaches it.

    Linux is not a supported host, so the `/mnt/` branch is unreachable from a
    delivery run. It stays with the Linux development entry point, and this is
    the specification it has to keep.
    """

    assert "under ~/" in platform_warning("linux", Path("/mnt/c/proj"))


def test_linux_filesystem_paths_are_quiet():
    """The same unreachable path stays quiet off a Windows-mounted drive."""

    assert platform_warning("linux", Path("/home/me/proj")) is None

def test_captured_output_is_text_unless_bytes_are_asked_for(tmp_path):
    # git paths are UTF-8 whatever the locale, so discover asks for bytes.
    script = "import sys; sys.stdout.buffer.write(b'ok\\n'); sys.stderr.buffer.write(b'warn\\n')"
    text = run([sys.executable, "-c", script], tmp_path, capture=True)
    raw = run([sys.executable, "-c", script], tmp_path, capture=True, text=False)
    assert (text.stdout, text.stderr) == ("ok\n", "warn\n")
    assert (raw.stdout, raw.stderr) == (b"ok\n", b"warn\n")
