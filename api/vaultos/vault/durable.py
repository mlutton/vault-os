"""Atomic, durable publication of authoritative JSON files.

On first use of a fresh state root, an existing ancestor created by another
process or a concurrent thread in the same process but not yet made durable
is not re-synced. A cached directory removed and re-created at runtime is
also not re-synced if the cache hit sees a directory: it checks only
`is_dir()`, not directory identity. These are accepted durability residuals.
"""

import json
import os
import uuid
from pathlib import Path

_durable_directories: set[Path] = set()


def ensure_durable_dir(path: Path) -> None:
    """Persist the requested entry and missing ancestors; cache while present."""
    path = path.resolve()
    if path in _durable_directories:
        if path.is_dir():
            return
        _durable_directories.discard(path)
    if not path.parent.is_dir():
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
