# FILE: src/mint/skills/__init__.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Skill prompt registry: select and render prompts by model tier and format
#   SCOPE: Skill file lookup, prompt loading, design token injection
#   DEPENDS: none
#   LINKS: docs/knowledge-graph.xml#M-SKILLS, docs/verification-plan.xml#V-M-SKILLS
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   SkillRegistry - registry for skill prompt lookup and rendering
#   select_skill - find skill file by tier and format
#   render_prompt - load prompt and inject design tokens
#   skills - module-level registry instance
# END_MODULE_MAP

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

VALID_TIERS = ("small", "medium", "frontier")
VALID_FORMATS = ("docx", "pptx")
TOKENS_PLACEHOLDER = "{{DESIGN_TOKENS}}"


class SkillNotFoundError(Exception):
    pass


class TokensInvalidError(Exception):
    pass


@dataclass(frozen=True)
class SkillRef:
    tier: str
    format: str
    path: Path

    def __str__(self) -> str:
        return f"{self.tier}/{self.format}"


class SkillRegistry:
    def __init__(self, skills_dir: Path) -> None:
        self._skills_dir = skills_dir

    # START_BLOCK_SCAN_SKILLS
    def scan(self) -> list[SkillRef]:
        if not self._skills_dir.is_dir():
            return []
        refs: list[SkillRef] = []
        for tier_dir in sorted(self._skills_dir.iterdir()):
            if not tier_dir.is_dir() or tier_dir.name not in VALID_TIERS:
                continue
            for prompt_file in sorted(tier_dir.glob("*.md")):
                fmt = prompt_file.stem
                if fmt in VALID_FORMATS:
                    refs.append(
                        SkillRef(tier=tier_dir.name, format=fmt, path=prompt_file)
                    )
        return refs
    # END_BLOCK_SCAN_SKILLS

    # START_BLOCK_SELECT_SKILL
    def select_skill(self, tier: str, format: str) -> SkillRef:
        if tier not in VALID_TIERS:
            raise SkillNotFoundError(
                f"Unknown tier '{tier}'. Valid tiers: {VALID_TIERS}"
            )
        if format not in VALID_FORMATS:
            raise SkillNotFoundError(
                f"Unknown format '{format}'. Valid formats: {VALID_FORMATS}"
            )
        target = self._skills_dir / tier / f"{format}.md"
        if not target.is_file():
            raise SkillNotFoundError(
                f"No skill prompt found for tier='{tier}' format='{format}' "
                f"at {target}"
            )
        logger.info(
            "[Skills][select_skill][BLOCK_SELECT_SKILL] Selected skill: %s/%s",
            tier,
            format,
        )
        return SkillRef(tier=tier, format=format, path=target)
    # END_BLOCK_SELECT_SKILL

    # START_BLOCK_RENDER_PROMPT
    def render_prompt(
        self, skill: SkillRef, design_tokens: dict[str, Any] | None = None
    ) -> str:
        prompt_text = skill.path.read_text(encoding="utf-8")
        if design_tokens is not None:
            tokens_json = json.dumps(design_tokens, indent=2)
            prompt_text = prompt_text.replace(TOKENS_PLACEHOLDER, tokens_json)
        else:
            prompt_text = prompt_text.replace(TOKENS_PLACEHOLDER, "{}")
        logger.info(
            "[Skills][render_prompt][BLOCK_RENDER_PROMPT] "
            "Rendered skill: %s/%s, tokens_injected=%s",
            skill.tier,
            skill.format,
            design_tokens is not None,
        )
        return prompt_text
    # END_BLOCK_RENDER_PROMPT
