"""An interrupted run ends every process its coverage command started.

On Linux the terminal sends SIGINT to the whole foreground process group, so
this is native Windows behaviour: crapper puts each command's process tree in
a Job Object and ends the job when the run is interrupted or crapper dies.
"""

import _thread
import json
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


def test_batch_arguments_are_literal_through_npm_style_percent_star(tmp_path):
    from crapper.platform import run

    batch = tmp_path / 'probe.cmd'
    batch.write_bytes(
        b'@echo off\r\n'
        b'node -e "console.log(JSON.stringify(process.argv.slice(1)))" %*\r\n')
    arguments = ['%SPEC_LITERAL_ARG%', 'space here', 'a&b', 'a|b', 'a^b', 'a!b', '',
                 'a"b', 'trail\\', 'quote" & percent%CD% | unicode中', 'slash\\"quote']
    result = run([str(batch), *arguments], tmp_path, capture=True,
                 env={**os.environ, 'SPEC_LITERAL_ARG': 'expanded'})
    assert result.returncode == 0
    assert json.loads(result.stdout) == arguments


def test_batch_arguments_are_literal_in_an_argv_command(tmp_path):
    from crapper.platform import run

    batch = tmp_path / 'probe.cmd'
    batch.write_bytes(b'@echo off\r\nsetlocal\r\necho [%~1]\r\n')
    result = run([str(batch), '%SPEC_LITERAL_ARG%'], tmp_path, capture=True,
                 env={**os.environ, 'SPEC_LITERAL_ARG': 'expanded'})
    assert result.returncode == 0
    assert result.stdout == '[%SPEC_LITERAL_ARG%]\n'


def test_extensionless_explicit_batch_path_uses_its_launcher(tmp_path):
    from crapper.platform import run

    batch = tmp_path / 'probe.cmd'
    batch.write_bytes(b'@echo off\r\nexit /b 0\r\n')
    assert run([str(tmp_path / 'probe')], tmp_path, capture=True).returncode == 0


def test_installed_vitest_uses_its_node_entry_with_literal_argv(tmp_path):
    from crapper.runners import typescript_command
    from crapper.platform import run

    (tmp_path / 'package.json').write_text('{"scripts":{"test":"vitest run"}}')
    binary = tmp_path / 'node_modules' / '.bin'
    binary.mkdir(parents=True)
    (binary / 'vitest').write_text('#!/bin/sh\nexit 99\n')
    (binary / 'vitest.cmd').write_bytes(b'@exit /b 99\r\n')
    package = tmp_path / 'node_modules' / 'vitest'
    package.mkdir()
    (package / 'package.json').write_text('{"bin":{"vitest":"vitest.mjs"}}')
    (package / 'vitest.mjs').write_text('console.log(JSON.stringify(process.argv.slice(2)))')
    command = typescript_command(tmp_path, [], tmp_path / 'report %CD% & spaced')
    result = run(command, tmp_path, capture=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == command[1:]


def test_timeout_ends_captured_command_and_its_descendants(tmp_path):
    from crapper.platform import run

    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run(SPAWNER, tmp_path, capture=True, timeout=0.4)
    assert time.monotonic() - started < 2
    assert _ends_within(_pid(tmp_path / 'grandchild.pid'), 2)


def test_captured_output_returns_at_leader_exit_with_inherited_pipes(tmp_path):
    from crapper.platform import run

    script = (
        "import subprocess, sys; "
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(4)']); "
        "open('grandchild.pid', 'w').write(str(p.pid)); "
        "print('leader output', flush=True); print('leader error', file=sys.stderr, flush=True)"
    )
    started = time.monotonic()
    result = run([sys.executable, '-c', script], tmp_path, capture=True)
    assert time.monotonic() - started < 2
    assert result.returncode == 0
    assert result.stdout == 'leader output\n'
    assert result.stderr == 'leader error\n'
    assert _ends_within(_pid(tmp_path / 'grandchild.pid'), 2)


def test_captured_interrupt_ends_descendants_and_drains_output(tmp_path):
    from crapper.platform import run

    def interrupt_once_started():
        _pid(tmp_path / 'grandchild.pid')
        _thread.interrupt_main()

    threading.Thread(target=interrupt_once_started, daemon=True).start()
    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        run(SPAWNER, tmp_path, capture=True)
    assert time.monotonic() - started < 2
    assert _ends_within(_pid(tmp_path / 'grandchild.pid'), 2)


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
