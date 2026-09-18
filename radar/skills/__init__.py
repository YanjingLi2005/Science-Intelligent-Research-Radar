"""CCFA-style skill layer for the Research Radar agent.

Each skill has a clear owner, trigger boundary, and artifact contract.
Skills do not mutate shared state directly; they produce structured outputs
that the orchestration layer reconciles.
"""

from radar.skills.base import SkillOutput, SkillProtocol
from radar.skills.registry import SkillRegistry, get_registry

__all__ = ["SkillOutput", "SkillProtocol", "SkillRegistry", "get_registry"]
