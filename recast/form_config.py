"""Configuration for form-to-CSV conversion (one .md file → one CSV row)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import yaml


@dataclass
class FieldSpec:
    label: str   # as it appears in VLM output (e.g. "STATION")
    column: str  # output CSV column name (e.g. "station")


@dataclass
class FormConfig:
    name: str
    filename_patterns: list[str]
    fields: list[FieldSpec]


def load_form_config(path: Path) -> FormConfig:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    fields = [
        FieldSpec(label=f["label"], column=f["column"])
        for f in (data.get("fields") or [])
    ]
    return FormConfig(
        name=data.get("name", path.stem),
        filename_patterns=data.get("filename_patterns", []),
        fields=fields,
    )


def label_to_slug(label: str) -> str:
    """'HISTORICAL SURVEY' → 'historical_survey'"""
    s = label.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def write_draft_config(
    path: Path,
    name: str,
    field_counts: list[tuple[str, int]],
    total_files: int,
) -> None:
    """Write a draft YAML config; counts appear as comments so the user knows what's reliable."""
    lines = [
        f"name: {name}",
        "filename_patterns:",
        '  - ""  # add a filename substring to match, or pass -c path/to/this.yaml directly',
        "",
        "# Rename 'column' values as needed. Remove fields you don't want.",
        "# Count shows how many of the scanned files contained this field.",
        "fields:",
    ]
    for label, count in field_counts:
        slug = label_to_slug(label)
        lines.append(f"  # {count}/{total_files} files")
        lines.append(f'  - label: "{label}"')
        lines.append(f"    column: {slug}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
