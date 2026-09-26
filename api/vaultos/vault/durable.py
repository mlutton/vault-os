"""Atomic, durable publication of authoritative JSON files."""

import json
import os
import uuid
from pathlib import Path


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


def write_record(path: Path, record: dict, *, exclusive: bool = False) -> None:
    """Publish a complete JSON record, including its directory entry."""
    path.parent.mkdir(parents=True, exist_ok=True)
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
