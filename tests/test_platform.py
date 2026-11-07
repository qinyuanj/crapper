from pathlib import Path

from crapper.cli import platform_warning
from crapper.platform import MINIMUM_WINDOWS_BUILD, refusal


def test_windows_11_runs_natively():
    assert refusal("win32", 26220) is None


def test_windows_10_22h2_is_the_minimum_build():
    assert MINIMUM_WINDOWS_BUILD == 19045
    assert refusal("win32", 19045) is None


def test_older_windows_is_refused_naming_the_minimum_build():
    message = refusal("win32", 19044)
    assert message == (
        "crapper needs Windows 10 22H2 (build 19045) or later; this is build 19044. "
        "Update Windows, or run crapper inside WSL2."
    )


def test_linux_is_never_refused():
    assert refusal("linux", None) is None


def test_native_windows_paths_are_quiet():
    assert platform_warning("win32", Path("C:/proj")) is None


def test_windows_drive_under_wsl_suggests_the_linux_filesystem():
    assert "under ~/" in platform_warning("linux", Path("/mnt/c/proj"))


def test_linux_filesystem_paths_are_quiet():
    assert platform_warning("linux", Path("/home/me/proj")) is None
