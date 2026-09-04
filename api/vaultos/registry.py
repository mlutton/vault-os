import json
from dataclasses import dataclass, field
from pathlib import Path


class RegistryError(RuntimeError):
    pass


class SubmissionError(RuntimeError):
    def __init__(self, field: str, message: str):
        self.field = field
        super().__init__(message)


@dataclass(frozen=True)
class SkillArg:
    name: str
    required: bool
    type: str
    max_length: int | None = None


@dataclass(frozen=True)
class Skill:
    id: str
    label: str
    deck: bool
    engine: str | None
    args: tuple[SkillArg, ...] = field(default_factory=tuple)
    # Opaque, engine-specific configuration (e.g. the script engine's `argv`
    # template and `deliverable` path template -- see
    # vaultos/runner/engines/script.py). The platform never reads its
    # contents; only the engine adapter named by `engine` interprets it. This
    # keeps adding a runtime to one adapter plus one registry row (per the
    # runner spec) instead of growing named fields on Skill for every engine.
    engine_config: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Registry:
    version: int
    skills: tuple[Skill, ...]

    def get(self, skill_id: str) -> Skill | None:
        for skill in self.skills:
            if skill.id == skill_id:
                return skill
        return None


def load_registry(vault_root: Path) -> Registry:
    path = vault_root / "system" / "skills.json"
    if not path.exists():
        raise RegistryError(f"skill registry not found at {path}")
    data = json.loads(path.read_text())

    skills = tuple(
        Skill(
            id=s["id"],
            label=s["label"],
            deck=s["deck"],
            engine=s.get("engine"),
            engine_config=s.get("engine_config", {}),
            args=tuple(
                SkillArg(
                    name=a["name"],
                    required=a["required"],
                    type=a["type"],
                    max_length=a.get("max_length"),
                )
                for a in s.get("args", [])
            ),
        )
        for s in data["skills"]
    )

    ids = [s.id for s in skills]
    if len(ids) != len(set(ids)):
        raise RegistryError("duplicate skill id in registry")

    return Registry(version=data["version"], skills=skills)


def validate_submission(registry: Registry, skill_id: str, args: dict) -> Skill:
    skill = registry.get(skill_id)
    if skill is None:
        raise SubmissionError("skill", f"unknown skill: {skill_id}")

    known = {a.name for a in skill.args}
    for key in args:
        if key not in known:
            raise SubmissionError(key, f"unknown arg: {key}")

    for arg in skill.args:
        value = args.get(arg.name)
        if arg.required and not value:
            raise SubmissionError(arg.name, f"missing required arg: {arg.name}")
        if value is not None and arg.type == "string" and not isinstance(value, str):
            raise SubmissionError(arg.name, f"{arg.name} must be a string")
        if (
            value is not None
            and arg.max_length is not None
            and isinstance(value, str)
            and len(value) > arg.max_length
        ):
            raise SubmissionError(arg.name, f"{arg.name} exceeds max_length {arg.max_length}")

    return skill
