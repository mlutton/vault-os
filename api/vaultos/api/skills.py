from fastapi import APIRouter, Depends, HTTPException

from ..registry import Registry
from .deps import get_registry, get_settings

router = APIRouter()


def _skill_to_dict(skill) -> dict:
    return {
        "id": skill.id,
        "label": skill.label,
        "deck": skill.deck,
        "engine": skill.engine,
        "args": [
            {"name": a.name, "required": a.required, "type": a.type, "max_length": a.max_length}
            for a in skill.args
        ],
    }


@router.get("/skills")
def list_skills(registry: Registry = Depends(get_registry), settings=Depends(get_settings)):
    if not settings.vault_readable():
        raise HTTPException(503, detail="vault root is missing or unreadable")
    return {"version": registry.version, "skills": [_skill_to_dict(s) for s in registry.skills]}
