"""Atomic snapshot publication through the report writer's public interface."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

from crapper import metrics
from crapper.model import Entry


def test_publication_replaces_previous_snapshot_with_complete_tree(tmp_path):
    root = tmp_path / "project with spaces"
    destination = root / ".metrics" / "crap.edn"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"previous snapshot containing deleted sources\r\n")
    entries = [Entry("choose", "demo.core", 2, 100.0, 2.0, "core.lua", "lua")]

    assert metrics.write_metrics(entries, root) == destination

    assert destination.read_bytes() == (
        b'{:entries [\n'
        b'  {:name "choose", :namespace "demo.core", :complexity 2, '
        b':coverage 100.0, :crap 2.0}\n]}\n'
    )
    assert list(destination.parent.iterdir()) == [destination]


@pytest.mark.parametrize("existing", [False, True])
def test_rendering_failure_preserves_destination_without_staging(tmp_path, existing):
    root = tmp_path / "project with spaces"
    destination = root / ".metrics" / "crap.edn"
    original = b"previous snapshot\x00\xff\r\n"
    if existing:
        destination.parent.mkdir(parents=True)
        destination.write_bytes(original)
    # A string that cannot be represented in the snapshot's UTF-8 encoding.
    entries = [Entry("bad\ud800", "demo", 1, None, None, "core.lua", "lua")]

    with pytest.raises(UnicodeEncodeError):
        metrics.write_metrics(entries, root)

    if existing:
        assert destination.read_bytes() == original
        assert list(destination.parent.iterdir()) == [destination]
    else:
        assert not root.exists()


@pytest.mark.parametrize("existing", [False, True])
def test_replacement_failure_preserves_destination_and_cleans_staging(
    tmp_path, monkeypatch, existing
):
    root = tmp_path / "project with spaces"
    destination = root / ".metrics" / "crap.edn"
    destination.parent.mkdir(parents=True)
    original = b"previous snapshot\x00\xff\r\n"
    if existing:
        destination.write_bytes(original)

    def fail_replace(source, target):
        source, target = Path(source), Path(target)
        assert source.parent == destination.parent
        assert source.read_bytes() == b"{:entries []}\n"
        assert target == destination
        if existing:
            assert destination.read_bytes() == original
        else:
            assert not destination.exists()
        raise PermissionError("destination is locked")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(PermissionError, match="destination is locked"):
        metrics.write_metrics([], root)

    assert destination.read_bytes() == original if existing else not destination.exists()
    assert set(destination.parent.iterdir()) == ({destination} if existing else set())


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["create", "write", "close"])
def test_staging_failure_preserves_destination_and_cleans_temporary_files(
    tmp_path, monkeypatch, existing, failure
):
    root = tmp_path / "project with spaces"
    destination = root / ".metrics" / "crap.edn"
    destination.parent.mkdir(parents=True)
    original = b"previous snapshot\x00\xff\r\n"
    if existing:
        destination.write_bytes(original)
    real_temporary_file = tempfile.NamedTemporaryFile

    class FailingStage:
        def __init__(self, stream):
            self.stream = stream
            self.name = stream.name

        def __enter__(self):
            return self

        def write(self, payload):
            if failure == "write":
                self.stream.write(payload[:3])
                raise OSError("staging failed")
            return self.stream.write(payload)

        def __exit__(self, *args):
            self.stream.close()
            if failure == "close":
                raise OSError("staging failed")

    def failing_temporary_file(*args, **kwargs):
        if failure == "create":
            raise OSError("staging failed")
        return FailingStage(real_temporary_file(*args, **kwargs))

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", failing_temporary_file)
    with pytest.raises(OSError, match="staging failed"):
        metrics.write_metrics([], root)

    assert destination.read_bytes() == original if existing else not destination.exists()
    assert set(destination.parent.iterdir()) == ({destination} if existing else set())


def test_empty_tree_publication_removes_all_previous_entries(tmp_path):
    destination = tmp_path / ".metrics" / "crap.edn"
    destination.parent.mkdir()
    destination.write_bytes(b'{:entries [{:name "deleted"}]}\n')

    assert metrics.write_metrics([], tmp_path) == destination
    assert destination.read_bytes() == b"{:entries []}\n"
    assert list(destination.parent.iterdir()) == [destination]
