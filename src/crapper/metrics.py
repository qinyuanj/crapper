"""Write the crap4clj snapshot uml-viewer reads."""

import os
import tempfile
from pathlib import Path

from crapper.model import Entry


def metrics_path(root: Path) -> Path:
    return root / ".metrics" / "crap.edn"


def _edn_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _edn_number(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    if "." not in text:
        text += ".0"
    return text


def _edn_value(value: float | None) -> str:
    if value is None:
        return "nil"
    return _edn_number(value)


def render_edn(entries: list[Entry]) -> str:
    """EDN map `{:entries [...]}` with crap4clj's keys, in crap4clj's order.

    uml-viewer groups this file by `:namespace` and joins each operation on
    `:name`. Coverage is a percentage. `nil` coverage and CRAP mean N/A.
    """

    rows = []
    for entry in entries:
        rows.append(
            "  {"
            f":name {_edn_string(entry.name)}, "
            f":namespace {_edn_string(entry.namespace)}, "
            f":complexity {entry.complexity}, "
            f":coverage {_edn_value(entry.coverage)}, "
            f":crap {_edn_value(entry.crap)}"
            "}"
        )
    body = "\n".join(rows)
    if body:
        return "{:entries [\n" + body + "\n]}\n"
    return "{:entries []}\n"


def write_metrics(entries: list[Entry], root: Path) -> Path:
    """Replace the snapshot atomically, or propagate failure without changing it.

    Render and encode before staging beside the destination. Closing the staged
    file before replacement also permits native Windows publication. An empty
    tree publishes an empty entries map, removing all previous metrics.
    """
    payload = render_edn(entries).encode("utf-8")
    path = metrics_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            staged = Path(stream.name)
            stream.write(payload)
        os.replace(staged, path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
    return path
