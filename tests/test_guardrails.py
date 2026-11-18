"""Repository rules that review kept catching by hand.

Fast and tool-free, so the pre-commit hook runs them on every commit.
"""

import ast
import re
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Modules allowed to start processes, relative to src/, each with its reason.
PROCESS_BOUNDARY = {
    "crapper/platform.py": "the platform boundary: argv resolution, batch quoting, Windows Job Objects",
}

# Calls that start a process, by module and attribute; a trailing * is a prefix.
_LAUNCHERS = {
    "subprocess": ("Popen", "run", "call", "check_call", "check_output", "getoutput", "getstatusoutput"),
    "os": ("system", "popen", "spawn*", "exec*", "posix_spawn*"),
    "asyncio": ("create_subprocess_*",),
}


def _launcher(module: str, name: str) -> bool:
    return any(name.startswith(pattern[:-1]) if pattern.endswith("*") else name == pattern
               for pattern in _LAUNCHERS.get(module, ()))


def _imported_names(tree: ast.Module) -> dict[str, tuple[str, str | None]]:
    """Local name -> (module, attribute) for every import of a launcher module or launcher."""

    names = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _LAUNCHERS:
                    names[alias.asname or alias.name] = (alias.name, None)
        elif isinstance(node, ast.ImportFrom) and node.module in _LAUNCHERS:
            for alias in node.names:
                names[alias.asname or alias.name] = (node.module, alias.name)
    return names


def _launches(path: Path) -> list[int]:
    """Lines of `path` that call a process-launching API."""

    tree = ast.parse(path.read_bytes(), filename=str(path))
    names = _imported_names(tree)
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            module, attribute = names.get(func.value.id, (None, None))
            target = (module, func.attr) if attribute is None else (None, None)
        elif isinstance(func, ast.Name):
            target = names.get(func.id, (None, None))
        else:
            continue
        if target[0] is not None and target[1] is not None and _launcher(*target):
            lines.append(node.lineno)
    return lines


def test_processes_start_only_inside_the_platform_boundary():
    source = ROOT / "src"
    violations = [
        f"src/{path.relative_to(source).as_posix()}:{line}"
        for path in sorted(source.rglob("*.py"))
        if path.relative_to(source).as_posix() not in PROCESS_BOUNDARY
        for line in _launches(path)
    ]
    assert not violations, (
        "These lines start a process outside the platform boundary; call crapper.platform.run "
        "(Job Objects, argv resolution, batch quoting) instead:\n  " + "\n  ".join(violations)
    )


def test_the_boundary_names_only_existing_modules():
    assert all((ROOT / "src" / module).is_file() for module in PROCESS_BOUNDARY)


@pytest.mark.parametrize("source, expected", [
    ("import subprocess\nsubprocess.run(['x'])\n", [2]),
    ("import subprocess as sp\nsp.check_output(['x'])\n", [2]),
    ("from subprocess import Popen as P\nP(['x'])\n", [2]),
    ("import os\nos.system('x')\nos.execvp('x', ['x'])\nos.spawnl(0, 'x')\n", [2, 3, 4]),
    ("import asyncio\nasyncio.create_subprocess_exec('x')\n", [2]),
    ("import subprocess\nsubprocess.CompletedProcess(['x'], 0)\nx: subprocess.Popen\n", []),
    ("import os\nos.replace('a', 'b')\nos.environ.get('x')\n", []),
])
def test_launches_are_found_through_any_import_spelling(tmp_path, source, expected):
    path = tmp_path / "module.py"
    path.write_text(source, encoding="utf-8")
    assert sorted(_launches(path)) == expected


def test_readme_and_help_document_the_same_exit_codes():
    from crapper.cli import HELP

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    table = set(re.findall(r"^\| (\d+) \|", readme, flags=re.MULTILINE))
    paragraph = HELP.split("Exit status:", 1)[1].split("\n\n", 1)[0]
    help_codes = set(re.findall(r"\b(\d+) (?:when|for)\b", paragraph))
    assert table and table == help_codes, (
        f"README exit table lists {sorted(table)}, the help's Exit status paragraph lists "
        f"{sorted(help_codes)}. Document every exit code in both."
    )


def _tracked(*pathspecs: str) -> list[str]:
    listed = subprocess.run(["git", "ls-files", "-z", "--", *pathspecs], cwd=ROOT, capture_output=True)
    if listed.returncode != 0:
        pytest.skip("not a git checkout")
    return [name for name in listed.stdout.decode("utf-8").split("\0") if name]


def test_golden_files_are_checked_out_with_lf():
    crlf = [name for name in _tracked("tests/fixtures", "*.edn") if b"\r" in (ROOT / name).read_bytes()]
    assert not crlf, (
        "These tracked golden files contain CR bytes, so byte-for-byte comparisons will fail:\n  "
        + "\n  ".join(crlf)
        + "\nFix: keep `*.edn` and `tests/fixtures/**` as `text eol=lf` in .gitattributes, then delete "
        "those working copies and re-checkout them: git checkout -- <files>"
    )


_MESSAGE_COMMANDS = {"throw", "Write-Host", "Write-Output", "echo"}
_VALUE_FLAGS = {"--tree", "--lua-version", "--deps-mode", "--server", "--namespace"}
_ROCK_VERSION = re.compile(r"\d[\w.-]*\Z")

# luafilesystem is named only on Windows: LuaCov loads lfs directly there, before
# Busted resolves its own dependency on it.
WINDOWS_ONLY_ROCKS = {"luafilesystem"}


def _command_lines(script: str) -> list[str]:
    """The script's lines with shell backslash continuations joined."""

    lines: list[str] = []
    pending = ""
    for line in script.splitlines():
        if line.rstrip().endswith("\\"):
            pending += line.rstrip()[:-1] + " "
            continue
        lines.append(pending + line)
        pending = ""
    if pending:
        lines.append(pending)
    return lines


def _rocks_installed(script: str) -> dict[str, str]:
    """Rock name -> pinned version for every luarocks install command of a setup script."""

    rocks: dict[str, str] = {}
    for line in _command_lines(script):
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            continue
        # A message that names luarocks is not an install, and an apt-get line that
        # installs luarocks itself is not one either: luarocks must be the command.
        if not words or words[0].startswith("#") or words[0] in _MESSAGE_COMMANDS:
            continue
        if "install" not in words:
            continue
        installing = words.index("install")
        if not any(word.lower().endswith(("luarocks", "luarocks.exe")) for word in words[:installing]):
            continue
        rock = ""
        rest = words[installing + 1:]
        while rest:
            word = rest.pop(0)
            if word.startswith("-"):
                if word in _VALUE_FLAGS and rest:
                    rest.pop(0)
                continue
            if word.startswith("$"):
                continue  # a variable, not a rock this test can compare
            if rock and _ROCK_VERSION.match(word):
                rocks[rock] = word
                continue
            rock = word
            rocks.setdefault(rock, "")
    return rocks


def test_the_two_setup_scripts_install_the_same_rocks():
    """A rock one setup script installs and the other does not is a platform that silently skips its tests."""

    ubuntu = _rocks_installed((ROOT / "scripts" / "setup-ubuntu.sh").read_text(encoding="utf-8"))
    windows = _rocks_installed((ROOT / "scripts" / "setup-windows.ps1").read_text(encoding="utf-8"))
    assert ubuntu, "no luarocks install line was found in scripts/setup-ubuntu.sh"
    missing = sorted(set(ubuntu) - set(windows))
    assert not missing, (
        "scripts/setup-windows.ps1 does not install " + ", ".join(missing)
        + ", so Windows skips the Lua tests that need them"
    )
    unexpected = sorted(set(windows) - set(ubuntu) - WINDOWS_ONLY_ROCKS)
    assert not unexpected, (
        "scripts/setup-windows.ps1 installs " + ", ".join(unexpected)
        + ", which setup-ubuntu.sh does not; add it there, or to WINDOWS_ONLY_ROCKS with the reason"
    )
    mismatched = sorted(
        f"{rock}: {ubuntu[rock]} in setup-ubuntu.sh, {windows[rock]} in setup-windows.ps1"
        for rock in set(ubuntu) & set(windows)
        if ubuntu[rock] != windows[rock]
    )
    assert not mismatched, "The two setup scripts pin different versions: " + " | ".join(mismatched)
CONTINUED = "sudo luarocks --lua-version=5.4 " + "\\" + "\n  install luacov\n"


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ("luarocks install busted\n", {"busted": ""}),
        ("luarocks install --force busted\n", {"busted": ""}),
        ("luarocks install busted luacov\n", {"busted": "", "luacov": ""}),
        ("luarocks install cluacov 1.0.0-1\n", {"cluacov": "1.0.0-1"}),
        ("luarocks install 'cluacov' '1.0.0-1'\n", {"cluacov": "1.0.0-1"}),
        ("sudo luarocks --lua-version=5.4 --tree ~/.luarocks install --deps-mode none luacov-reporter-lcov\n",
         {"luacov-reporter-lcov": ""}),
        (CONTINUED, {"luacov": ""}),
        ('throw "luarocks install $Name exited $ExitCode"' + "\n", {}),
        ("ROCK=luacov; luarocks install $ROCK\n", {}),
        ("# luarocks install a rock by hand\n", {}),
    ],
)
def test_rocks_are_found_through_any_install_spelling(script: str, expected: dict[str, str]):
    """The guardrail is only as good as the spellings it reads, so each of these has to survive."""

    assert _rocks_installed(script) == expected
