"""Skill protocol and base implementations."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class SkillOutput:
    skill_name: str
    artifact_type: str
    content: dict
    warnings: list[str] = field(default_factory=list)
    handoff_suggestions: list[str] = field(default_factory=list)


@runtime_checkable
class SkillProtocol(Protocol):
    name: str
    description: str
    triggers: list[str]
    not_for: list[str]

    def can_handle(self, request: dict) -> bool: ...
    def execute(self, request: dict, *, project_state: dict) -> SkillOutput: ...


_PROMPT_DIR = Path(__file__).parents[1] / "llm" / "prompts" / "skills"


def _skill_prompt(skill_name: str, payload: dict) -> str:
    import json
    path = _PROMPT_DIR / f"{skill_name}.txt"
    if path.exists():
        instructions = path.read_text(encoding="utf-8").strip()
        return f"{instructions}\n\nINPUT:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    # A missing instruction file degrades the prompt silently; make it loud so
    # a renamed/removed prompt is noticed instead of shipping bare JSON.
    logger.warning("skill prompt file missing: %s", path)
    return json.dumps(payload, ensure_ascii=False, indent=2)
