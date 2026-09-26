"""`python -m vaultos.runner` -- the long-running runner entrypoint."""

import logging
import sys

from ..config import Settings
from ..db.conn import connect
from ..registry import load_registry
from .core import Runner, RunnerLockHeldError


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    conn = connect(settings.db_path)
    registry = load_registry(settings.vault_root)
    try:
        runner = Runner(conn, registry, settings)
        runner.run_forever()
        return 0
    except (RunnerLockHeldError, ValueError) as exc:
        print(f"runner: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
