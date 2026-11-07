"""What differs between the hosts crapper runs on: Linux and native Windows.

Every OS-specific decision lives here, selected once from `sys.platform`, so
analysis and report code stay platform-agnostic.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

WINDOWS = sys.platform == "win32"

# Forked workers inherit the loaded parsers instead of importing them again.
# Windows has no fork, so its workers start fresh and load parsers themselves.
POOL_START_METHOD = "spawn" if WINDOWS else "fork"

# Windows 10 22H2. Older builds are refused before any work.
MINIMUM_WINDOWS_BUILD = 19045

# Where a virtual environment keeps its interpreter, and the interpreter to use
# without one. Windows has no python3.exe outside the Microsoft Store alias.
VENV_PYTHON = Path("Scripts", "python.exe") if WINDOWS else Path("bin", "python")
DEFAULT_PYTHON = "python" if WINDOWS else "python3"


def windows_build() -> int | None:
    """The host's Windows build number, or None off Windows."""

    return sys.getwindowsversion().build if WINDOWS else None


def refusal(platform: str, build: int | None) -> str | None:
    """Why crapper will not run on `platform` at Windows `build`, or None when it will."""

    if platform != "win32" or build is None or build >= MINIMUM_WINDOWS_BUILD:
        return None
    return (
        f"crapper needs Windows 10 22H2 (build {MINIMUM_WINDOWS_BUILD}) or later; "
        f"this is build {build}. Update Windows, or run crapper inside WSL2."
    )


# Windows refuses to replace a file another process holds open without
# FILE_SHARE_DELETE (as Python's open() and most editors do): access denied (5)
# or sharing violation (32). Those are transient; anything else is not.
_TRANSIENT_WINERRORS = {5, 32}
_REPLACE_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.0, 1.0)


def replace(staged: Path, destination: Path) -> None:
    """`os.replace`, retried a few seconds on Windows while a reader holds `destination`."""

    import time

    for delay in (*_REPLACE_DELAYS, None) if WINDOWS else (None,):
        try:
            os.replace(staged, destination)
            return
        except PermissionError as exc:
            if delay is None or getattr(exc, "winerror", None) not in _TRANSIENT_WINERRORS:
                raise
            time.sleep(delay)


def lf_streams() -> None:
    """Print LF on every host; Windows text streams otherwise write CRLF."""

    for stream in (sys.stdout, sys.stderr):
        if WINDOWS and hasattr(stream, "reconfigure"):
            stream.reconfigure(newline="\n")


def resolve(argv: list[str]) -> list[str]:
    """argv with its program found on PATH the way the shell would.

    CreateProcess only appends `.exe`, so on Windows `npm`, `npx` and `mvn`
    (`.cmd` launchers) are looked up through PATHEXT first. A `.cmd`/`.bat`
    program still parses its own arguments with cmd.exe rules; crapper only
    passes it generated paths and flags.
    """

    if WINDOWS and argv and not os.path.dirname(argv[0]):
        return [shutil.which(argv[0]) or argv[0], *argv[1:]]
    return argv


def run(command: str | list[str], cwd: Path, capture: bool = False,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run argv without a shell, or a command string in the host shell (sh, cmd.exe).

    `capture` holds text output. On Windows the process tree runs in a Job
    Object, so an interrupt, an exception, or crapper's own death ends every
    process the command started. OSError means the program did not start.
    """

    shell = isinstance(command, str)
    options: dict = {"cwd": cwd, "shell": shell, "env": env}
    if capture:
        options.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    argv = command if shell else resolve(command)
    if not WINDOWS:
        return subprocess.run(argv, **options)
    return _run_in_job(argv, options)


# --- Windows Job Objects -----------------------------------------------------

_CREATE_SUSPENDED = 0x00000004
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
# A blocking wait does not see Ctrl+C on Windows, so waits poll this often.
_POLL_SECONDS = 0.2


def _kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel32


def _limit_information():
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    info = Extended()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    return info


class _Job:
    """A kill-on-close Job Object: closing the last handle ends every process in it."""

    def __init__(self):
        import ctypes

        self._kernel32 = _kernel32()
        self._handle = self._kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _limit_information()
        if not self._kernel32.SetInformationJobObject(
                self._handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def adopt(self, process: subprocess.Popen) -> None:
        """Put a suspended process in the job, then let it run, so no child escapes."""

        import ctypes

        handle = int(process._handle)  # Popen has no public accessor for its process handle.
        if not self._kernel32.AssignProcessToJobObject(self._handle, handle):
            raise ctypes.WinError(ctypes.get_last_error())
        # Popen closed the primary thread handle; resume through the process.
        if ctypes.WinDLL("ntdll").NtResumeProcess(ctypes.c_void_p(handle)) != 0:
            raise OSError("cannot resume the coverage process")

    def terminate(self, code: int = 1) -> None:
        self._kernel32.TerminateJobObject(self._handle, code)

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def _run_in_job(argv: str | list[str], options: dict) -> subprocess.CompletedProcess:
    job = _Job()
    try:
        process = subprocess.Popen(argv, creationflags=_CREATE_SUSPENDED, **options)
        try:
            job.adopt(process)
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=_POLL_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            job.terminate()
            process.kill()
            process.communicate()
            raise
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    finally:
        job.close()
