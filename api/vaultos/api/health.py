from fastapi import APIRouter, Depends

from ..registry import Registry
from ..vault.runner import read_heartbeat
from .deps import get_registry, get_settings

router = APIRouter()


@router.get("/health")
def health(settings=Depends(get_settings), registry: Registry = Depends(get_registry)):
    ok = settings.vault_readable()
    heartbeat = read_heartbeat(settings.vault_root) if ok else None
    return {
        "ok": ok,
        "vault_root": str(settings.vault_root),
        "registry_version": registry.version,
        "runner": {
            "alive": bool(heartbeat and heartbeat.alive),
            "ts": heartbeat.ts if heartbeat else None,
        },
    }
