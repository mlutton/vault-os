"""`python -m vaultos.runner` -- the runner process entrypoint. Long-running;
treat as a daemon (see api/CLAUDE.md's note on Fable-Os-Web's runner.js for
the legacy equivalent this replaces)."""

import logging

from ..config import Settings
from ..db.conn import connect
from ..registry import load_registry
from .core import Runner


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    conn = connect(settings.db_path)
    registry = load_registry(settings.vault_root)
    runner = Runner(conn, registry, settings)
    runner.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
