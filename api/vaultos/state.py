from pathlib import Path

from .config import Settings


def resolve_state_root(settings: Settings) -> Path:
    """The directory runtime-state files (the runner's heartbeat, per-run
    logs) resolve under: VAULTOS_STATE_ROOT when set, else the legacy
    <VAULT_ROOT>/system location.

    Only vaultos.runner honors this in v1. The existing spine's own
    file-based paths (vault.intents.write_intent, vault.runner.read_heartbeat,
    jobs.reconcile.reconcile_from_files -- all still literally
    `vault_root/system/...`) are NOT rerouted through this helper: migrating
    them is the private folder-migration choreography referenced by the
    runner spec's "Out of Scope" section, gated on cutover. This function
    exists so the runner's own new paths are ready to land in the right place
    once that migration flips the legacy paths over too -- until then, with
    VAULTOS_STATE_ROOT unset (the common case), this resolves to the exact
    same `vault_root/system` directory those legacy paths already use, so the
    runner's heartbeat is visible to the existing GET /runner endpoint with
    no further wiring."""
    if settings.state_root_override is not None:
        return settings.state_root_override
    return settings.vault_root / "system"
