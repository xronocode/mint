from pathlib import Path

import pytest

from mint.skills import (
    SkillNotFoundError,
    SkillRef,
    SkillRegistry,
)

FIXTURES_SKILLS = Path(__file__).parent.parent.parent.parent / "skills"


class TestScan:
    def test_scan_finds_all_skills(self) -> None:
        reg = SkillRegistry(FIXTURES_SKILLS)
        refs = reg.scan()
        tiers_found = {r.tier for r in refs}
        formats_found = {r.format for r in refs}
        assert "frontier" in tiers_found
        assert "medium" in tiers_found
        assert "small" in tiers_found
        assert "docx" in formats_found
        assert "pptx" in formats_found

    def test_scan_empty_dir(self, tmp_path: Path) -> None:
        reg = SkillRegistry(tmp_path / "nonexistent")
        assert reg.scan() == []

    def test_scan_ignores_invalid_files(self, tmp_path: Path) -> None:
        tier_dir = tmp_path / "frontier"
        tier_dir.mkdir()
        (tier_dir / "docx.md").write_text("prompt")
        (tier_dir / "readme.txt").write_text("not a skill")
        reg = SkillRegistry(tmp_path)
        refs = reg.scan()
        assert len(refs) == 1
        assert refs[0].format == "docx"


class TestSelectSkill:
    def test_select_frontier_docx(self) -> None:
        reg = SkillRegistry(FIXTURES_SKILLS)
        ref = reg.select_skill("frontier", "docx")
        assert ref.tier == "frontier"
        assert ref.format == "docx"
        assert ref.path.is_file()

    def test_select_small_pptx(self) -> None:
        reg = SkillRegistry(FIXTURES_SKILLS)
        ref = reg.select_skill("small", "pptx")
        assert ref.tier == "small"
        assert ref.format == "pptx"
        assert ref.path.is_file()

    def test_select_unknown_tier_raises(self) -> None:
        reg = SkillRegistry(FIXTURES_SKILLS)
        with pytest.raises(SkillNotFoundError, match="Unknown tier"):
            reg.select_skill("huge", "docx")

    def test_select_unknown_format_raises(self) -> None:
        reg = SkillRegistry(FIXTURES_SKILLS)
        with pytest.raises(SkillNotFoundError, match="Unknown format"):
            reg.select_skill("frontier", "xlsx")

    def test_select_missing_skill_file_raises(self, tmp_path: Path) -> None:
        reg = SkillRegistry(tmp_path)
        with pytest.raises(SkillNotFoundError, match="No skill prompt found"):
            reg.select_skill("frontier", "docx")


class TestRenderPrompt:
    def test_render_without_tokens(self) -> None:
        reg = SkillRegistry(FIXTURES_SKILLS)
        ref = reg.select_skill("frontier", "docx")
        result = reg.render_prompt(ref)
        assert "{{DESIGN_TOKENS}}" not in result
        assert "docx" in result.lower()

    def test_render_with_tokens(self) -> None:
        reg = SkillRegistry(FIXTURES_SKILLS)
        ref = reg.select_skill("frontier", "docx")
        tokens = {"colors": {"primary": "#E6007E"}, "typography": {"heading": "Arial"}}
        result = reg.render_prompt(ref, design_tokens=tokens)
        assert "#E6007E" in result
        assert "Arial" in result
        assert "{{DESIGN_TOKENS}}" not in result

    def test_render_injects_valid_json(self) -> None:
        reg = SkillRegistry(FIXTURES_SKILLS)
        ref = reg.select_skill("medium", "pptx")
        tokens = {"colors": {"primary": "#000000"}}
        result = reg.render_prompt(ref, design_tokens=tokens)
        marker = result[result.index("#000000") - 5 : result.index("#000000") + 10]
        assert "#000000" in marker

    def test_render_replaces_placeholder_with_empty_on_none(self) -> None:
        reg = SkillRegistry(FIXTURES_SKILLS)
        ref = reg.select_skill("small", "docx")
        result = reg.render_prompt(ref, design_tokens=None)
        assert "{{DESIGN_TOKENS}}" not in result


class TestSkillRef:
    def test_str_representation(self) -> None:
        ref = SkillRef(tier="frontier", format="docx", path=Path("/x.md"))
        assert str(ref) == "frontier/docx"
