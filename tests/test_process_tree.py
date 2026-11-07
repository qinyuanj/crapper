"""An interrupted run ends every process its coverage command started.

On Linux the terminal sends SIGINT to the whole foreground process group, so
this is native Windows behaviour: crapper puts each command's process tree in
a Job Object and ends the job when the run is interrupted or crapper dies.
"""

import _thread
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from host_shell import python

from crapper.runners import run_shell

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Job Objects are Windows-only")

SOURCE = Path(__file__).resolve().parents[1] / "src"

# The command starts a grandchild that would outlive it, records its pid, then waits.
SPAWNER = python(
    "import subprocess, sys, time; "
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)']); "
    "open('grandchild.pid', 'w').write(str(child.pid)); time.sleep(60)"
)


def _pid(path: Path, deadline: float = 30.0) -> int:
    start = time.monotonic()
    while time.monotonic() - start < deadline:
        if path.is_file() and path.read_text():
            return int(path.read_text())
        time.sleep(0.05)
    raise AssertionError(f"{path} was never written")


def _ends_within(pid: int, seconds: float) -> bool:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return True  # Already gone.
    try:
        return kernel32.WaitForSingleObject(ctypes.c_void_p(handle), int(seconds * 1000)) == 0
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def test_ctrl_c_during_a_command_ends_its_whole_process_tree(tmp_path):
    pid_file = tmp_path / "grandchild.pid"

    def interrupt_once_started():
        _pid(pid_file)
        _thread.interrupt_main()

    threading.Thread(target=interrupt_once_started, daemon=True).start()
    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        run_shell(SPAWNER, tmp_path)
    # The command would sleep 60 s and the grandchild 600 s; the interrupt must not wait for either.
    assert time.monotonic() - started < 30
    assert _ends_within(_pid(pid_file), 10)


def test_a_killed_crapper_takes_its_coverage_processes_with_it(tmp_path):
    project = tmp_path / "project with spaces"
    (project / "src").mkdir(parents=True)
    (project / "src" / "calc.py").write_text("def value():\n    return 42\n", encoding="utf-8")
    environment = {**os.environ, "PYTHONPATH": str(SOURCE)}
    crapper = subprocess.Popen(
        [sys.executable, "-m", "crapper", "--root", str(project), "--coverage-command", SPAWNER],
        env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        grandchild = _pid(project / "grandchild.pid")
    finally:
        crapper.kill()
        crapper.wait()
    assert _ends_within(grandchild, 10)
