"""Keep a mutant from launching coverage against the project under test.

`run()` falls through into `run_coverage` when a mutant skips the help return.
That would delete `target/coverage` and recurse into this suite.
"""

import subprocess

import pytest


@pytest.fixture(autouse=True)
def do_not_launch_project_coverage(monkeypatch):
    monkeypatch.setattr("crapper.cli.run_coverage", lambda *_args, **_kwargs: None)


@pytest.fixture
def git():
    """Run git in a directory with a throwaway identity."""

    def run(root, *args):
        config = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
        subprocess.run(["git", *config, *args], cwd=root, check=True, capture_output=True)

    return run
