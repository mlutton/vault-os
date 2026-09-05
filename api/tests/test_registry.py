import json

import pytest

from vaultos.registry import RegistryError, SubmissionError, load_registry, validate_submission

REGISTRY_JSON = {
    "version": 1,
    "skills": [
        {
            "id": "metrics-pull",
            "label": "Pull Metrics",
            "deck": True,
            "engine": "claude",
            "args": [],
        },
        {
            "id": "deep-research",
            "label": "Deep Research",
            "deck": True,
            "engine": "claude",
            "args": [{"name": "topic", "required": True, "type": "string", "max_length": 500}],
        },
    ],
}


def _write_registry(vault_root, data=REGISTRY_JSON):
    system = vault_root / "system"
    system.mkdir(parents=True, exist_ok=True)
    (system / "skills.json").write_text(json.dumps(data))


def test_load_registry(tmp_path):
    _write_registry(tmp_path)
    registry = load_registry(tmp_path)
    assert registry.version == 1
    assert {s.id for s in registry.skills} == {"metrics-pull", "deep-research"}
    deep_research = registry.get("deep-research")
    assert deep_research.args[0].name == "topic"
    assert deep_research.args[0].required is True
    assert registry.get("no-such-skill") is None


def test_load_registry_missing_file(tmp_path):
    with pytest.raises(RegistryError):
        load_registry(tmp_path)


def test_load_registry_rejects_duplicate_ids(tmp_path):
    dup = {"version": 1, "skills": [REGISTRY_JSON["skills"][0], REGISTRY_JSON["skills"][0]]}
    _write_registry(tmp_path, dup)
    with pytest.raises(RegistryError):
        load_registry(tmp_path)


def test_validate_submission_ok(tmp_path):
    _write_registry(tmp_path)
    registry = load_registry(tmp_path)
    skill = validate_submission(registry, "deep-research", {"topic": "AI agents"})
    assert skill.id == "deep-research"


def test_validate_submission_unknown_skill(tmp_path):
    _write_registry(tmp_path)
    registry = load_registry(tmp_path)
    with pytest.raises(SubmissionError) as exc:
        validate_submission(registry, "not-a-skill", {})
    assert exc.value.field == "skill"


def test_validate_submission_missing_required_arg(tmp_path):
    _write_registry(tmp_path)
    registry = load_registry(tmp_path)
    with pytest.raises(SubmissionError) as exc:
        validate_submission(registry, "deep-research", {})
    assert exc.value.field == "topic"


def test_validate_submission_unknown_arg_key(tmp_path):
    _write_registry(tmp_path)
    registry = load_registry(tmp_path)
    with pytest.raises(SubmissionError) as exc:
        validate_submission(registry, "deep-research", {"topic": "x", "bogus": "y"})
    assert exc.value.field == "bogus"


def test_validate_submission_no_args_required(tmp_path):
    _write_registry(tmp_path)
    registry = load_registry(tmp_path)
    skill = validate_submission(registry, "metrics-pull", {})
    assert skill.id == "metrics-pull"


@pytest.mark.parametrize("bad_value", [{"nested": "dict"}, ["a", "list"], 123, 4.5, True])
def test_validate_submission_rejects_non_string_for_string_arg(tmp_path, bad_value):
    _write_registry(tmp_path)
    registry = load_registry(tmp_path)
    with pytest.raises(SubmissionError) as exc:
        validate_submission(registry, "deep-research", {"topic": bad_value})
    assert exc.value.field == "topic"


def test_validate_submission_still_accepts_valid_string_value(tmp_path):
    _write_registry(tmp_path)
    registry = load_registry(tmp_path)
    skill = validate_submission(registry, "deep-research", {"topic": "AI agents"})
    assert skill.id == "deep-research"


def test_validate_submission_still_enforces_max_length_for_valid_string(tmp_path):
    _write_registry(tmp_path)
    registry = load_registry(tmp_path)
    with pytest.raises(SubmissionError) as exc:
        validate_submission(registry, "deep-research", {"topic": "x" * 501})
    assert exc.value.field == "topic"
