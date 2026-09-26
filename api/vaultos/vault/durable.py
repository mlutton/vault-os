"""Atomic, durable publication of authoritative JSON files."""

import json
import os
import uuid
from pathlib import Path

_durable_directories: set[Path] = set()


def ensure_durable_dir(path: Path) -> None:
    """Persist uncached ancestor entries once, including existing directories."""
    path = path.resolve()
    if path in _durable_directories:
        return
    if path.parent != path:
        ensure_durable_dir(path.parent)
    path.mkdir(exist_ok=True)
    parent = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    _durable_directories.add(path)


def sync_record(path: Path) -> None:
    """Make an observed record durable before acknowledging a duplicate.

    A concurrent publisher may have linked its file but not yet fsynced the
    directory; existence alone does not establish durable acknowledgment.
    """
    with path.open("rb") as stream:
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    parent = os.open(path.parent.parent, os.O_RDONLY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def write_record(path: Path, record: dict, *, exclusive: bool = False) -> None:
    """Publish a complete JSON record, including its directory entry."""
    ensure_durable_dir(path.parent)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x") as stream:
            json.dump(record, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            # Linking publishes the fully fsynced file without replacing an owner.
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
