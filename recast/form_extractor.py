"""Extract field values from VLM form output (.md files).

Handles three label formats produced by Chandra/Chandra-2:
  bold_md:   **LABEL:** value
  bold_html: <b>LABEL:</b> value  (inside HTML table cells)
  line:      LABEL: value  (plain text, label at line start or after whitespace)

The extractor tries all three and returns the first non-empty match.
"""

from __future__ import annotations

import re
from pathlib import Path

from recast.form_config import FormConfig

try:
    from bs4 import BeautifulSoup
    _BS4 = True
except ImportError:
    _BS4 = False

# ── Pattern builders ──────────────────────────────────────────────────────────

def _bold_md_re(label: str) -> re.Pattern:
    return re.compile(
        r'\*\*' + re.escape(label) + r':\*\*\s*(.*?)(?=\*\*[^*\n]+:\*\*|\Z)',
        re.DOTALL | re.IGNORECASE,
    )

def _bold_html_re(label: str) -> re.Pattern:
    return re.compile(
        r'<b>' + re.escape(label) + r':</b>\s*(.*?)(?=<b>|</td>|</th>|\Z)',
        re.DOTALL | re.IGNORECASE,
    )

def _line_re(label: str) -> re.Pattern:
    # Label at start of line or after a table cell boundary; value runs to newline
    return re.compile(
        r'(?:^|(?<=\n)|(?<=\t))\s*' + re.escape(label) + r':\s*([^\n<]*)',
        re.IGNORECASE,
    )

# ── Scan a single file for all field labels ───────────────────────────────────

_BOLD_MD_LABEL = re.compile(r'\*\*([^*\n]+?):\*\*')
_BOLD_HTML_LABEL = re.compile(r'<b>([^<\n]+?):</b>', re.IGNORECASE)

def scan_labels(path: Path) -> set[str]:
    content = path.read_text(encoding="utf-8", errors="replace")
    found: set[str] = set()
    for m in _BOLD_MD_LABEL.finditer(content):
        found.add(m.group(1).strip())
    for m in _BOLD_HTML_LABEL.finditer(content):
        found.add(m.group(1).strip())
    return found


def scan_all_labels(paths: list[Path]) -> tuple[list[tuple[str, int]], int]:
    """Return [(label, count_of_files), ...] sorted by count desc, and total file count."""
    counts: dict[str, int] = {}
    for p in paths:
        for label in scan_labels(p):
            counts[label] = counts.get(label, 0) + 1
    return sorted(counts.items(), key=lambda x: -x[1]), len(paths)


# ── Extract one row from a file ───────────────────────────────────────────────

def _clean(value: str) -> str:
    value = re.sub(r'\n+', ' ', value)
    value = re.sub(r' {2,}', ' ', value)
    # Strip HTML tags that leak into value text
    value = re.sub(r'<[^>]+>', '', value)
    return value.strip()


def _extract_field(content: str, label: str) -> str:
    for pattern in (_bold_md_re(label), _bold_html_re(label), _line_re(label)):
        m = pattern.search(content)
        if m:
            v = _clean(m.group(1))
            if v:
                return v
    return ""


def extract_row(path: Path, config: FormConfig) -> dict[str, str]:
    """Extract one CSV row from a form .md file."""
    content = path.read_text(encoding="utf-8", errors="replace")
    row: dict[str, str] = {"source_file": path.stem}
    for field in config.fields:
        row[field.column] = _extract_field(content, field.label)
    return row


# ── Resolve input path to list of .md files ───────────────────────────────────

def resolve_md_paths(input_path: Path) -> list[Path]:
    """Mirror csvify's resolution: subdirs with name/name.md, or .md files directly."""
    input_path = input_path.resolve()
    if input_path.is_file():
        return [input_path] if input_path.suffix == ".md" else []

    results = []
    for item in sorted(input_path.iterdir()):
        if item.is_dir():
            md = item / f"{item.name}.md"
            if md.exists():
                results.append(md)

    if not results:
        results = sorted(input_path.glob("*.md"))

    return results
