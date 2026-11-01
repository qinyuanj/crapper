"""Replace a file in one step so readers never see a partial write."""

import os
import tempfile
from pathlib import Path


def write_bytes(path: Path, payload: bytes) -> None:
    """Stage `payload` beside `path`, then rename it into place.

    A failure at any step leaves the previous file untouched and removes the
    staged copy. The staged file is closed before the rename, which keeps the
    swap atomic, and made world-readable, since mkstemp creates it 0600.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as stream:
            staged = Path(stream.name)
            stream.write(payload)
        os.chmod(staged, 0o644)
        os.replace(staged, path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def write_text(path: Path, text: str) -> None:
    """Encode first, so text that cannot be written never stages a file."""

    write_bytes(path, text.encode("utf-8"))
