"""Repository rules that review kept catching by hand.

Fast and tool-free, so CI runs them on every push.
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


_WILDCARD = re.compile(r"[*?\[]")


def _dead_directory_diagnoses(attributes: str) -> list[str]:
    """One diagnosis string per .gitattributes rule whose literal directory is gone.

    A diagnosis, not a directory: the value names the rule and the directory it
    used to govern, because that is what the caller prints.
    """

    dead: list[str] = []
    for line in attributes.splitlines():
        words = line.split()
        if not words or words[0].startswith("#"):
            continue
        pattern = words[0]
        parts = pattern.split("/")[:-1]
        for index, part in enumerate(parts):
            if _WILDCARD.search(part):
                break
            directory = "/".join(parts[: index + 1])
            if not (ROOT / directory).is_dir():
                dead.append(pattern + ": " + directory + " is not a directory in this repo")
                break
    return dead


def test_gitattributes_rules_name_directories_that_exist():
    """A rule for a directory this repo no longer has governs nothing, and keeps a dead workflow looking current."""

    dead = _dead_directory_diagnoses((ROOT / ".gitattributes").read_text(encoding="utf-8"))
    assert not dead, (
        "These .gitattributes rules name directories that are gone, so they govern nothing and keep a "
        "removed workflow looking current:\n  " + "\n  ".join(dead) + "\nDelete each rule with the "
        "directory it governed."
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("# a comment\ntests/fixtures/** text eol=lf\n", []),
        ("*.edn text eol=lf\n*.cmd text eol=crlf\n", []),
        (".githooks/** text eol=lf\n", [".githooks/**: .githooks is not a directory in this repo"]),
        ("src/**/*.py text eol=lf\n", []),
    ],
)
def test_dead_directories_are_found_through_any_rule_spelling(text: str, expected: list[str]):
    """The guardrail is only as good as the spellings it reads, so each of these has to survive."""

    assert _dead_directory_diagnoses(text) == expected


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


def test_the_supported_setup_installs_every_rock_the_linux_script_does():
    """setup-windows.ps1 is the only installation path; setup-ubuntu.sh is the kept Linux entry point.

    The check still runs both ways, because a rock one script pins and the other
    does not is a host that silently skips the Lua tests needing it — and that
    host is now Windows, which has no second setup path to fall back on.
    """

    ubuntu = _rocks_installed((ROOT / "scripts" / "setup-ubuntu.sh").read_text(encoding="utf-8"))
    windows = _rocks_installed((ROOT / "scripts" / "setup-windows.ps1").read_text(encoding="utf-8"))
    assert ubuntu, "no luarocks install line was found in scripts/setup-ubuntu.sh"
    missing = sorted(set(ubuntu) - set(windows))
    assert not missing, (
        "scripts/setup-windows.ps1 does not install " + ", ".join(missing)
        + ", so Windows — the only installation path — skips the Lua tests that need them"
    )
    unexpected = sorted(set(windows) - set(ubuntu) - WINDOWS_ONLY_ROCKS)
    assert not unexpected, (
        "scripts/setup-windows.ps1 installs " + ", ".join(unexpected)
        + ", which the Linux entry point does not; add it to setup-ubuntu.sh, or to "
        "WINDOWS_ONLY_ROCKS with the reason"
    )
    mismatched = sorted(
        f"{rock}: {ubuntu[rock]} in setup-ubuntu.sh, {windows[rock]} in setup-windows.ps1"
        for rock in set(ubuntu) & set(windows)
        if ubuntu[rock] != windows[rock]
    )
    assert not mismatched, (
        "The two setup scripts pin different rocks at different versions, so Windows and the "
        "Linux entry point measure Lua differently: " + " | ".join(mismatched)
    )


# The toolchain each setup script must provide, whichever host runs the suite. Some tools
# are spelled differently on the two hosts — `lua5.4` is Ubuntu's package, `lua.exe` the
# interpreter setup-windows.ps1 builds — so each entry lists the spellings both scripts
# use for the same thing. The rock comparison above cannot see any of these.
SHARED_TOOLS = {
    "Lua 5.4": ("lua5.4", "lua.exe"),
    "LuaRocks": ("luarocks",),
    "Busted": ("busted",),
    "LuaCov": ("luacov",),
    "cluacov": ("cluacov",),
    "luacov-reporter-lcov": ("luacov-reporter-lcov",),
}

# Tools only the kept Linux development entry point provides, each with its reason.
# `bb` is here because the suite reads the snapshot with its own EDN reader (ADR 0007),
# so the supported host installs no second language runtime for one assertion.
ENTRY_POINT_ONLY_TOOLS = {
    "bb": "the suite reads EDN itself (ADR 0007), so the supported host does not install it",
}


def _mentions(text: str, tool: str) -> bool:
    """Whether `text` names `tool` as a command, not as part of a longer word."""

    return re.search(rf"(?<![\w.-]){re.escape(tool)}(?![\w.-])", text) is not None


def test_both_setup_scripts_provide_the_toolchain_the_suite_needs():
    scripts = {
        "scripts/setup-ubuntu.sh": (ROOT / "scripts" / "setup-ubuntu.sh").read_text(encoding="utf-8"),
        "scripts/setup-windows.ps1": (ROOT / "scripts" / "setup-windows.ps1").read_text(encoding="utf-8"),
    }
    missing = [
        f"{tool} in {name}"
        for tool, spellings in SHARED_TOOLS.items()
        for name, text in scripts.items()
        if not any(spelling in text for spelling in spellings)
    ]
    assert not missing, (
        "These setup scripts do not provide a tool the suite needs, so the host running them "
        "fails or skips what depends on it: " + ", ".join(missing)
    )


def test_tools_only_the_linux_entry_point_provides_are_declared_in_both_directions():
    ubuntu = (ROOT / "scripts" / "setup-ubuntu.sh").read_text(encoding="utf-8")
    windows = (ROOT / "scripts" / "setup-windows.ps1").read_text(encoding="utf-8")
    for tool, reason in ENTRY_POINT_ONLY_TOOLS.items():
        assert _mentions(ubuntu, tool), (
            f"scripts/setup-ubuntu.sh no longer provides {tool}, which is declared entry-point "
            f"only ({reason}); update the declaration"
        )
        assert not _mentions(windows, tool), (
            f"scripts/setup-windows.ps1 now provides {tool}, which is declared entry-point only "
            f"({reason}); add it to SHARED_TOOLS or drop the declaration"
        )


@pytest.mark.parametrize(
    ("text", "tool", "expected"),
    [
        ("command -v bb >/dev/null", "bb", True),
        ('sudo install -m 755 "$tmp/bb" /usr/local/bin/bb', "bb", True),
        ("tar -xzf bb.tgz", "bb", False),
        ("runs core.bb and bb.edn tasks", "bb", False),
    ],
)
def test_a_tool_is_recognised_as_a_command_not_as_part_of_another_word(text, tool, expected):
    assert _mentions(text, tool) is expected


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
