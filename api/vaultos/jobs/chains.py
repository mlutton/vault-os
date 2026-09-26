"""Stable identity for the existing fixed skill-chain edges."""

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class ChainRule:
    rule_id: str
    rule_version: int
    child_skill: str


CHAIN_NAMESPACE = uuid.UUID("7f6cc4ad-76b5-5d6e-a536-ef1bcb735e13")


def child_job_id(parent_attempt_id: str, rule_id: str, rule_version: int) -> str:
    return str(uuid.uuid5(CHAIN_NAMESPACE, f"{parent_attempt_id}:{rule_id}:{rule_version}"))
