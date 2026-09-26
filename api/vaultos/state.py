from pathlib import Path

from .config import Settings


def resolve_state_root(settings: Settings | Path) -> Path:
    """Resolve the shared root for skill-job files.

    Path callers use the current vault layout. Settings callers may carry an
    override; the runner refuses to start until it matches that layout.
    """
    if isinstance(settings, Path):
        return settings / "system"
    if settings.state_root_override is not None:
        return settings.state_root_override
    return settings.vault_root / "system"
