"""Custom coverage commands for the shell crapper hands them to on this host.

`--coverage-command` runs in the platform shell: cmd.exe on the supported host,
sh on the kept Linux development entry point. Fixtures build their commands here
so one test covers both spellings.
"""

import os
import shlex
import sys


def shell_command(argv: list[str]) -> str:
    """Quote argv so the host shell passes each argument through literally."""

    if os.name != "nt":
        return shlex.join(argv)
    # cmd.exe treats & | < > ^ as literal inside double quotes. It has no escape
    # for a double quote there, so fixtures avoid one.
    assert not any('"' in arg or "%" in arg for arg in argv), argv
    return " ".join(f'"{arg}"' for arg in argv)


def python(code: str) -> str:
    """A command that runs `code` in this interpreter, in either shell."""

    return shell_command([sys.executable, "-c", code])


def chain(*commands: str) -> str:
    """Run each command after the previous one succeeded; `&&` means that in both shells."""

    return " && ".join(commands)
