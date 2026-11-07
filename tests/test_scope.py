import os
from pathlib import Path

import pytest

from crapper.discover import (
    changed_files,
    exclude_files,
    excluded,
    exclusion_error,
    under_roots,
    without_excluded,
)


def _write(root: Path, relative: str, text: str = "x\n") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path.resolve()


def test_untracked_directories_are_expanded_in_a_repository_without_commits(tmp_path, git):
    git(tmp_path, "init", "-q")
    first = _write(tmp_path, "new/pkg/a.py")
    second = _write(tmp_path, "new/b.lua")
    _write(tmp_path, ".gitignore", "ignored.py\n")
    _write(tmp_path, "new/ignored.py")
    assert changed_files(tmp_path) == [(tmp_path / ".gitignore").resolve(), second, first]


def test_additions_edits_and_renames_are_kept_and_deletions_dropped(tmp_path, git):
    git(tmp_path, "init", "-q")
    for name in ("edit.py", "gone.py", "old.py", "same.py"):
        _write(tmp_path, name, f"{name}\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "base")
    edited = _write(tmp_path, "edit.py", "changed\n")
    git(tmp_path, "rm", "-q", "gone.py")
    git(tmp_path, "mv", "old.py", "renamed.py")
    staged = _write(tmp_path, "staged.py")
    git(tmp_path, "add", "staged.py")
    assert changed_files(tmp_path) == [edited, (tmp_path / "renamed.py").resolve(), staged]


def test_a_subdirectory_root_sees_only_its_own_files(tmp_path, git):
    git(tmp_path, "init", "-q")
    inside = _write(tmp_path, "sub/pkg/x.py")
    _write(tmp_path, "other/y.py")
    _write(tmp_path, "subway/z.py")
    assert changed_files(tmp_path / "sub") == [inside]


def test_names_git_would_quote_come_back_verbatim(tmp_path, git):
    git(tmp_path, "init", "-q")
    names = ["sp ace.py", "ünï.py", "sémi;colon.py"]
    if os.name != "nt":  # Windows forbids these characters in file names.
        names += ['quo"te.py', "new\nline.py", "tab\there.py"]
    expected = sorted((_write(tmp_path, name) for name in names), key=Path.as_posix)
    assert changed_files(tmp_path) == expected


def test_a_symlink_leading_outside_the_root_is_not_returned(tmp_path, git, symlink):
    project = tmp_path / "project"
    project.mkdir()
    git(project, "init", "-q")
    outside = _write(tmp_path, "outside.py")
    symlink(project / "link.py", outside)
    kept = _write(project, "kept.py")
    assert changed_files(project) == [kept]


def test_git_failure_is_reported_and_selects_nothing(tmp_path, monkeypatch, capsys):
    class Failed:
        returncode = 128
        stdout = b""
        stderr = b"fatal: not a git repository\n"

    monkeypatch.setattr("crapper.discover.subprocess.run", lambda *_a, **_k: Failed())
    assert changed_files(tmp_path) == []
    assert capsys.readouterr().err == "fatal: not a git repository\n"
    Failed.stderr = b""
    assert changed_files(tmp_path) == []
    assert capsys.readouterr().err == "git status failed\n"


def test_status_failure_after_the_toplevel_lookup_selects_nothing(tmp_path, monkeypatch, capsys):
    calls = []

    class Result:
        def __init__(self, returncode, stdout):
            self.returncode, self.stdout, self.stderr = returncode, stdout, b"status broke"

    def fake(command, **kwargs):
        calls.append((command, kwargs))
        return Result(0, f"{tmp_path}\n".encode()) if len(calls) == 1 else Result(1, b"?? a.py\0")

    monkeypatch.setattr("crapper.discover.subprocess.run", fake)
    _write(tmp_path, "a.py")
    assert changed_files(tmp_path) == []
    assert capsys.readouterr().err == "status broke\n"
    assert all(kwargs["cwd"] == tmp_path.resolve() for _command, kwargs in calls)
    assert all(kwargs["check"] is False and kwargs["capture_output"] is True for _c, kwargs in calls)


@pytest.mark.parametrize(
    "pattern",
    ["/abs", "..", "../x", "a/../b", "a\\..\\b", "\\abs", "a/.."],
)
def test_absolute_and_parent_patterns_are_usage_errors(pattern):
    message = exclusion_error(pattern)
    assert message == f"--exclude must be a path or glob inside the project root: {pattern}"


@pytest.mark.parametrize("pattern", ["a/b", "**/vendor", "./gen/", "..foo", "a..b/c", "*.py"])
def test_relative_patterns_are_accepted(pattern):
    assert exclusion_error(pattern) is None


@pytest.mark.parametrize(
    ("pattern", "relative", "expected"),
    [
        ("gen", "gen/a.py", True),
        ("gen", "src/gen/a.py", False),
        ("gen", "generated/a.py", False),
        ("**/gen", "src/gen/a.py", True),
        ("**/gen", "gen/a.py", True),
        ("src/*.py", "src/a.py", True),
        ("src/*.py", "src/x/a.py", False),
        ("src/?.py", "src/a.py", True),
        ("src/?.py", "src/ab.py", False),
        ("src/?", "src/a/b.py", True),
        ("src/**/a.py", "src/a.py", True),
        ("src/**/a.py", "src/x/y/a.py", True),
        ("src/**", "src/x/a.py", True),
        ("**", "a.py", True),
        ("SRC", "src/a.py", False),
        ("./src/", "src/a.py", True),
        ("src\\gen", "src/gen/a.py", True),
        ("src/a.py", "src/a.pyc", False),
        ("a.b", "axb/c.py", False),
        ("a+b", "a+b/c.py", True),
        ("*", "a.py", True),
    ],
)
def test_exclusion_globs_follow_uml_viewer(tmp_path, pattern, relative, expected):
    assert excluded(tmp_path / relative, tmp_path, [pattern]) is expected


def test_any_matching_pattern_excludes_and_outside_paths_never_do(tmp_path):
    assert excluded(tmp_path / "b/x.py", tmp_path, ["a", "b"]) is True
    assert excluded(tmp_path / "c/x.py", tmp_path, ["a", "b"]) is False
    assert excluded(tmp_path / "c/x.py", tmp_path, []) is False
    assert excluded(tmp_path.parent / "a/x.py", tmp_path, ["**"]) is False


def test_excluded_paths_are_compared_after_resolving_both_sides(tmp_path, symlink):
    real = tmp_path / "real"
    (real / "gen").mkdir(parents=True)
    linked = tmp_path / "linked"
    symlink(linked, real)
    assert excluded(real / "gen/a.py", linked, ["gen"]) is True
    assert excluded(linked / "gen/a.py", real, ["gen"]) is True


def test_exclude_files_keeps_order(tmp_path):
    files = [tmp_path / "z.py", tmp_path / "gen/a.py", tmp_path / "a.py"]
    assert exclude_files(files, tmp_path, ["gen"]) == [tmp_path / "z.py", tmp_path / "a.py"]
    assert exclude_files(files, tmp_path, []) == files


def test_files_in_directories_the_walk_skips_are_dropped(tmp_path, git):
    git(tmp_path, "init", "-q")
    for name in ("vendor/lib/v.py", "node_modules/p/n.py", ".uml-viewer/x/u.py", "src/build/b.py"):
        _write(tmp_path, name)
    kept = [_write(tmp_path, "src/a.py"), _write(tmp_path, "tests/test_a.py")]
    assert changed_files(tmp_path) == kept


def test_skipped_directories_are_judged_below_the_root(tmp_path, git):
    git(tmp_path, "init", "-q")
    inside = _write(tmp_path, "build/app/src/a.py")
    assert changed_files(tmp_path / "build" / "app") == [inside]


def test_under_roots_keeps_files_inside_any_root_in_order(tmp_path):
    files = [tmp_path / "src/b.py", tmp_path / "srcx/a.py", tmp_path / "lib/a.py", tmp_path / "top.py"]
    assert under_roots(files, [tmp_path / "lib", tmp_path / "src"]) == [files[0], files[2]]
    assert under_roots(files, [tmp_path / "top.py"]) == [files[3]]
    assert under_roots(files, []) == []


def test_under_roots_compares_after_resolving_both_sides(tmp_path, symlink):
    real = tmp_path / "real"
    (real / "src").mkdir(parents=True)
    linked = tmp_path / "linked"
    symlink(linked, real)
    assert under_roots([real / "src/a.py"], [linked / "src"]) == [real / "src/a.py"]
    assert under_roots([linked / "src/a.py"], [real / "src" / ".." / "src"]) == [linked / "src/a.py"]


def test_without_excluded_is_exclude_files(tmp_path):
    files = [tmp_path / "gen/a.py", tmp_path / "b.py"]
    assert without_excluded(files, tmp_path, ["gen"]) == exclude_files(files, tmp_path, ["gen"]) == [files[1]]
