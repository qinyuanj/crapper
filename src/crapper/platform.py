"""What differs between the hosts crapper runs on: Linux and native Windows.

Every OS-specific decision lives here, selected once from `sys.platform`, so
analysis and report code stay platform-agnostic.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from contextlib import contextmanager

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


def resolve(argv: list[str], cwd: Path | None = None,
            env: dict[str, str] | None = None) -> list[str]:
    """Resolve argv's program, including explicit extensionless Windows paths."""

    if not WINDOWS or not argv:
        return argv
    environment = os.environ if env is None else env
    program = argv[0]
    if os.path.dirname(program):
        if cwd is not None:
            program = str(cwd / program)
    else:
        program = shutil.which(program, path=environment.get("PATH")) or program
    # npm installs a POSIX shell script beside the Windows launcher. Prefer
    # PATHEXT even when that extensionless script exists at an explicit path.
    if os.path.dirname(program) and not Path(program).suffix:
        for extension in environment.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";"):
            if (candidate := Path(program + extension.lower())).is_file():
                program = str(candidate)
                break
    if Path(program).suffix.lower() in (".cmd", ".bat"):
        entry = _node_entry(Path(program))
        if entry is not None:
            node = Path(program).parent / "node.exe"
            return [str(node) if node.is_file() else shutil.which("node") or "node", str(entry), *argv[1:]]
    return [program, *argv[1:]]


def _node_entry(shim: Path) -> Path | None:
    """Known npm/npx and package-local Vitest launchers have native JS entries."""

    import json

    if shim.stem.lower() in ("npm", "npx"):
        entry = shim.parent / "node_modules" / "npm" / "bin" / f"{shim.stem.lower()}-cli.js"
        if entry.is_file():
            return entry
    if shim.parent.name == ".bin" and shim.stem == "vitest":
        package = shim.parent.parent / "vitest"
        try:
            bins = json.loads((package / "package.json").read_text(encoding="utf-8"))["bin"]
            entry = package / (bins["vitest"] if isinstance(bins, dict) else bins)
            if entry.is_file():
                return entry.resolve()
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return None


def command_line(argv: list[str], environment: dict[str, str]) -> str | list[str]:
    """Prepare literal Windows batch argv for Popen(shell=False).

    Values enter cmd through one environment expansion, which is not recursive.
    Always-quoted arguments survive a launcher's %* forwarding too. Native
    executables continue to use Python's normal Windows argv serialization.
    """

    if not WINDOWS or not argv or Path(argv[0]).suffix.lower() not in (".cmd", ".bat"):
        return argv
    import re
    import uuid

    prefix = "CRAPPER_ARG_" + uuid.uuid4().hex + "_"
    references = []
    for index, argument in enumerate(argv):
        if any(character in argument for character in "\r\n\0"):
            raise ValueError("Windows batch arguments cannot contain NUL or newlines")
        # cmd keeps doubled quotes quoted; the Windows C argv parser reads
        # them as a literal quote. Double backslashes before a quote as well.
        quoted = re.sub(r'(\\*)"', lambda match: match[1] * 2 + '""', argument)
        quoted += "\\" * (len(quoted) - len(quoted.rstrip("\\")))
        name = prefix + str(index)
        environment[name] = '"' + quoted + '"'
        references.append("%" + name + "%")
    comspec = os.environ.get("COMSPEC") or os.path.join(os.environ["SystemRoot"], "System32", "cmd.exe")
    return '"' + comspec + '" /d /v:off /s /c "' + " ".join(references) + '"'


def run(command: str | list[str], cwd: Path, capture: bool = False,
        env: dict[str, str] | None = None, timeout: float | None = None,
        text: bool = True) -> subprocess.CompletedProcess:
    """Run argv without a shell, or a command string in the host shell (sh, cmd.exe).

    `capture` holds the output: text, or bytes when `text` is False. On Windows
    the process tree runs in a Job Object, so an interrupt, an exception, or
    crapper's own death ends every process the command started. OSError means
    the program did not start.
    """

    shell = isinstance(command, str)
    options: dict = {"cwd": cwd, "shell": shell, "env": env}
    if capture:
        options.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if text:
            options.update(text=True, errors="replace")
    argv = command if shell else resolve(command, cwd, env)
    if not WINDOWS:
        return subprocess.run(argv, timeout=timeout, **options)
    if not shell:
        options["env"] = dict(os.environ if env is None else env)
        argv = command_line(argv, options["env"])
    return _run_in_job(argv, options, timeout)


# --- Windows Job Objects -----------------------------------------------------

_CREATE_SUSPENDED = 0x00000004
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
# A blocking wait does not see Ctrl+C on Windows, so waits poll this often.
_POLL_SECONDS = 0.2
_COMMAND = threading.local()


@contextmanager
def cancellable(stop: threading.Event):
    """Let the owning caller cancel this thread's Windows command trees."""

    previous = getattr(_COMMAND, "stop", None)
    _COMMAND.stop = stop
    try:
        yield
    finally:
        _COMMAND.stop = previous


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


def _run_in_job(argv: str | list[str], options: dict, timeout: float | None) -> subprocess.CompletedProcess:
    import time

    deadline = None if timeout is None else time.monotonic() + timeout
    job = _Job()
    stop = getattr(_COMMAND, "stop", None)
    try:
        process = subprocess.Popen(argv, creationflags=_CREATE_SUSPENDED, **options)
        try:
            job.adopt(process)
            while process.poll() is None:
                if deadline is not None and time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(argv, timeout)
                if stop is not None and stop.is_set():
                    job.terminate()
                try:
                    process.communicate(timeout=_POLL_SECONDS)
                except subprocess.TimeoutExpired:
                    pass
            # Descendants may still hold our pipe handles after the leader exits.
            # End their job before waiting for EOF; communicate retains everything
            # already read by its reader threads across timeout calls.
            job.close()
            stdout, stderr = process.communicate()
        except BaseException:
            job.terminate()
            process.kill()
            process.communicate()
            raise
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    finally:
        job.close()
