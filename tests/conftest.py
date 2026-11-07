"""Keep a mutant from launching coverage against the project under test.

`run()` falls through into `run_coverage` when a mutant skips the help return.
That would delete `target/coverage` and recurse into this suite.
"""

import subprocess
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def do_not_launch_project_coverage(monkeypatch):
    monkeypatch.setattr("crapper.cli.run_coverage", lambda *_args, **_kwargs: None)


@pytest.fixture
def symlink():
    """Create `link` pointing at `target`, or skip where this process may not.

    Windows needs Developer Mode or SeCreateSymbolicLinkPrivilege (error 1314
    without it), and must be told a link names a directory.
    """

    def make(link: Path, target: Path) -> Path:
        try:
            link.symlink_to(target, target_is_directory=target.is_dir())
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("this process may not create symlinks; enable Windows Developer Mode")
            raise
        return link

    return make


@pytest.fixture
def git():
    """Run git in a directory with a throwaway identity."""

    def run(root, *args):
        config = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
        subprocess.run(["git", *config, *args], cwd=root, check=True, capture_output=True)

    return run
