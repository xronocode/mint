# FILE: src/mint/templates/__init__.py
# VERSION: 0.1.0
# START_MODULE_CONTRACT
#   PURPOSE: Template engine for filling master OOXML templates from JSON content
#   SCOPE: Template discovery, placeholder replacement, design token application, ZIP repack
#   DEPENDS: M-CONFIG, M-VALIDATE
#   LINKS: docs/knowledge-graph.xml#M-TEMPLATES, docs/verification-plan.xml#V-M-TEMPLATES
# END_MODULE_CONTRACT
#
# START_MODULE_MAP
#   TemplateEngine - engine for template discovery and filling
#   TemplateMeta - metadata for a discovered template
#   list_templates - scan template directories for available templates
#   fill - fill a template with JSON content and design tokens
#   template_engine - module-level engine instance
# END_MODULE_MAP

# START_CHANGE_SUMMARY
#   LAST_CHANGE: v0.1.0 - Initial implementation
# END_CHANGE_SUMMARY

from __future__ import annotations

import logging
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")
TEMPLATE_EXTENSIONS = {".docx", ".pptx"}
TEMPLATE_DIRS = ("builtin", "extracted", "custom")


class TemplateNotFoundError(Exception):
    pass


class JSONSchemaError(Exception):
    pass


class PlaceholderMissingError(Exception):
    pass


@dataclass(frozen=True)
class TemplateMeta:
    name: str
    format: str
    path: Path
    source: str


@dataclass
class FillResult:
    output_path: Path
    placeholders_replaced: list[str] = field(default_factory=list)
    tokens_applied: bool = False


class TemplateEngine:
    def __init__(self, templates_dir: Path) -> None:
        self._templates_dir = templates_dir

    # START_BLOCK_LIST_TEMPLATES
    def list_templates(self) -> list[TemplateMeta]:
        if not self._templates_dir.is_dir():
            return []
        templates: list[TemplateMeta] = []
        for source_dir_name in TEMPLATE_DIRS:
            source_dir = self._templates_dir / source_dir_name
            if not source_dir.is_dir():
                continue
            for template_file in sorted(source_dir.iterdir()):
                if template_file.suffix.lower() not in TEMPLATE_EXTENSIONS:
                    continue
                fmt = template_file.suffix.lstrip(".").lower()
                name = template_file.stem
                templates.append(
                    TemplateMeta(
                        name=name, format=fmt, path=template_file, source=source_dir_name
                    )
                )
        return templates
    # END_BLOCK_LIST_TEMPLATES

    # START_BLOCK_FIND_TEMPLATE
    def find_template(self, name: str, fmt: str | None = None) -> TemplateMeta:
        for tmpl in self.list_templates():
            if tmpl.name == name and (fmt is None or tmpl.format == fmt):
                return tmpl
        raise TemplateNotFoundError(
            f"Template '{name}' (format={fmt}) not found in {self._templates_dir}"
        )
    # END_BLOCK_FIND_TEMPLATE

    # START_BLOCK_FILL
    def fill(
        self,
        template_meta: TemplateMeta,
        content_json: dict[str, Any],
        design_tokens: dict[str, Any] | None = None,
        output_path: Path | None = None,
    ) -> FillResult:
        src = template_meta.path
        if not src.is_file():
            raise TemplateNotFoundError(f"Template file not found: {src}")

        content_json = _flatten_json(content_json)

        if output_path is None:
            tmp_dir = Path(tempfile.mkdtemp(prefix="mint_template_"))
            output_path = tmp_dir / src.name

        with tempfile.TemporaryDirectory(prefix="mint_unzip_") as unzip_dir_str:
            unzip_dir = Path(unzip_dir_str)
            with zipfile.ZipFile(src, "r") as zf:
                from mint._security import validate_zip_paths

                validate_zip_paths(zf)
                zf.extractall(unzip_dir)

            replaced: list[str] = []
            for xml_file in unzip_dir.rglob("*.xml"):
                if not xml_file.is_file():
                    continue
                original = xml_file.read_text(encoding="utf-8")
                if "{{" not in original:
                    continue
                new_text, file_replaced = _replace_placeholders(original, content_json)
                if file_replaced:
                    xml_file.write_text(new_text, encoding="utf-8")
                    replaced.extend(file_replaced)

            if design_tokens:
                _apply_design_tokens(unzip_dir, design_tokens, template_meta.format)

            with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf_out:
                for file_path in unzip_dir.rglob("*"):
                    if file_path.is_file():
                        arcname = file_path.relative_to(unzip_dir)
                        zf_out.write(file_path, arcname)

        logger.info(
            "[Templates][fill][BLOCK_FILL] "
            "Filled template: %s, replaced=%d placeholders, tokens=%s",
            template_meta.name,
            len(replaced),
            design_tokens is not None,
        )
        return FillResult(
            output_path=output_path,
            placeholders_replaced=replaced,
            tokens_applied=design_tokens is not None,
        )
    # END_BLOCK_FILL


def _flatten_json(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten_json(value, full_key))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    flat.update(_flatten_json(item, f"{full_key}.{i}"))
                else:
                    flat[f"{full_key}.{i}"] = str(item)
        else:
            flat[full_key] = str(value)
    return flat


def _replace_placeholders(
    text: str, flat_content: dict[str, str]
) -> tuple[str, list[str]]:
    replaced: list[str] = []

    def _replacer(m: re.Match[str]) -> str:
        key = m.group(1)
        if key in flat_content:
            replaced.append(key)
            return flat_content[key]
        return m.group(0)

    new_text = PLACEHOLDER_PATTERN.sub(_replacer, text)
    return new_text, replaced


def _apply_design_tokens(
    unzip_dir: Path, tokens: dict[str, Any], fmt: str
) -> None:
    colors = tokens.get("colors", {})
    if not colors:
        return
    for xml_file in unzip_dir.rglob("*.xml"):
        if not xml_file.is_file():
            continue
        text = xml_file.read_text(encoding="utf-8")
        modified = False
        for token_name, token_value in colors.items():
            placeholder = f"{{{{ colors.{token_name} }}}}"
            if placeholder in text:
                text = text.replace(placeholder, str(token_value))
                modified = True
        if modified:
            xml_file.write_text(text, encoding="utf-8")
