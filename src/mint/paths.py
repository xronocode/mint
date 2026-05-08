from __future__ import annotations

from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_ROOT.parent.parent

SKILLS_DIR = _PROJECT_ROOT / "skills"
TEMPLATES_DIR = _PROJECT_ROOT / "templates"
RULES_DIR = _PROJECT_ROOT / "rules"
TOKENS_DIR = _PROJECT_ROOT / "tokens"
THEMES_DIR = _PACKAGE_ROOT / "themes"
