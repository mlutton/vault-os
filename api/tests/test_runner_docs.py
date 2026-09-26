"""The job authority contract stays discoverable from both surface specs."""

from pathlib import Path


def test_skill_job_authority_docs_link_runner_and_web():
    repo = Path(__file__).resolve().parents[2]
    specs = repo / "docs" / "specs"
    authority = (specs / "2026-09-25-skill-job-authority.md").read_text()
    runner = (specs / "2026-09-04-runner-engine-registry-design.md").read_text()
    web = (specs / "2026-09-04-web-v1-design.md").read_text()
    adr = (
        repo / "api" / "docs" / "adr" / "0022-modules-are-packages-with-a-registration-contract.md"
    ).read_text()

    assert "jobs` queued row" in authority
    assert "rebuildable" in authority.lower()
    assert "Finance" in authority
    assert "2026-09-25-skill-job-authority.md" in runner
    assert "2026-09-25-skill-job-authority.md" in web
    assert "ENGINE_REGISTRY" in adr
    assert "nothing in this repo ever branches on it" not in adr
