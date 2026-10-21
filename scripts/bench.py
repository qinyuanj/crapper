#!/usr/bin/env python3
"""Time crapper's three refresh modes on a project and compare snapshots.

    scripts/bench.py PROJECT [--reference DIR] [--record] [--crapper CMD] [-- ARGS...]

ARGS select the business tree, for example `--source-root src --exclude x`.
The modes run in order, so the existing-coverage run reads the full run's reports:

    full       --full-tree ARGS
    existing   --use-existing-coverage ARGS
    complexity --full-tree --no-coverage ARGS

Each mode's `.metrics/crap.edn` is compared byte for byte with `DIR/<mode>.edn`.
`--record` writes the references instead. Exit status is 1 when any mode fails
or any snapshot differs. The run writes `.metrics/` and `target/coverage/` in
PROJECT, so point it at a copy of a project you do not want touched.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import time
from pathlib import Path

MODES = (
    ("full", ["--full-tree"]),
    ("existing", ["--use-existing-coverage"]),
    ("complexity", ["--full-tree", "--no-coverage"]),
)


def _parse(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    forward: list[str] = []
    if "--" in argv:
        index = argv.index("--")
        argv, forward = argv[:index], argv[index + 1 :]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project", type=Path)
    parser.add_argument("--reference", type=Path, help="directory of <mode>.edn reference snapshots")
    parser.add_argument("--record", action="store_true", help="write the references instead of comparing")
    parser.add_argument("--crapper", default=f"{shlex.quote(sys.executable)} -m crapper",
                        help="command that runs crapper (default: this Python's crapper module)")
    parser.add_argument("--modes", default=",".join(name for name, _ in MODES),
                        help="comma-separated subset of full,existing,complexity")
    return parser.parse_args(argv), forward


def main(argv: list[str] | None = None) -> int:
    options, forward = _parse(sys.argv[1:] if argv is None else argv)
    project = options.project.resolve()
    snapshot = project / ".metrics" / "crap.edn"
    selected = set(options.modes.split(","))
    if options.record and options.reference is None:
        print("--record needs --reference", file=sys.stderr)
        return 1
    if options.record:
        options.reference.mkdir(parents=True, exist_ok=True)
    failed = False
    for name, mode_args in MODES:
        if name not in selected:
            continue
        command = [*shlex.split(options.crapper), "--root", str(project), *mode_args, *forward]
        started = time.perf_counter()
        completed = subprocess.run(command, capture_output=True, text=True)
        elapsed = time.perf_counter() - started
        verdict = f"exit {completed.returncode}"
        if completed.returncode not in (0, 2):
            failed = True
            sys.stderr.write(completed.stderr[-4000:])
        elif options.reference is not None:
            reference = options.reference / f"{name}.edn"
            if options.record:
                reference.write_bytes(snapshot.read_bytes())
                verdict += ", recorded"
            elif not reference.is_file():
                verdict += ", no reference"
            elif reference.read_bytes() == snapshot.read_bytes():
                verdict += ", identical"
            else:
                failed = True
                verdict += ", DIFFERS"
        print(f"{name:<10} {elapsed:7.2f} s  {verdict}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
