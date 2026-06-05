"""CLI for converting VLM form output to CSV (one .md file → one row).

Two-step workflow:
  Step 1: formify inspect PATH [-o config_draft.yaml]
          Scans .md files, finds all field labels, writes a draft config.
          Edit the draft: rename columns, delete unwanted fields.

  Step 2: formify convert PATH -c config.yaml [-o output.csv]
          Reads each .md file, extracts fields, writes one row per file.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import click

from recast.form_config import load_form_config, write_draft_config
from recast.form_extractor import (
    extract_row,
    resolve_md_paths,
    scan_all_labels,
)


@click.group()
def main():
    """VLM form output → CSV. One .md file per form → one row."""
    pass


@main.command("inspect")
@click.argument("path", type=click.Path(path_type=Path))
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None,
              help="Draft config output path (default: <name>_config.yaml in current dir).")
@click.option("--name", default="draft",
              help="Config name, also used as the default output filename stem.")
def inspect_cmd(path: Path, output: Path | None, name: str) -> None:
    """Scan form output files and write a draft config YAML.

    PATH is a directory containing .md files (or subdirs with name/name.md).
    """
    path = Path(path).resolve()
    md_paths = resolve_md_paths(path)
    if not md_paths:
        click.echo(f"No .md files found under {path}", err=True)
        sys.exit(1)

    field_counts, total = scan_all_labels(md_paths)
    if not field_counts:
        click.echo(
            "No **Label:** or <b>Label:</b> fields found. "
            "Check that VLM output uses bold field labels.",
            err=True,
        )
        sys.exit(1)

    out_path = Path(output) if output else Path(f"{name}_config.yaml")
    write_draft_config(out_path, name, field_counts, total)

    click.echo(f"Scanned {total} files — found {len(field_counts)} fields → {out_path}")
    click.echo("")
    click.echo("Next steps:")
    click.echo(f"  1. Edit {out_path}: rename 'column' values, remove unwanted fields")
    click.echo(f"  2. Run: formify convert {path} -c {out_path} -o output.csv")


@main.command("convert")
@click.argument("path", type=click.Path(path_type=Path))
@click.option("-c", "--config", "config_path", type=click.Path(path_type=Path),
              required=True, help="Config YAML (from 'formify inspect' or hand-written).")
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None,
              help="Output CSV path (default: <dir_name>_forms.csv next to input).")
def convert_cmd(path: Path, config_path: Path, output: Path | None) -> None:
    """Convert form .md files to CSV using a field config.

    PATH is a directory containing .md files (or subdirs with name/name.md).
    Each file becomes one row. Files with no matching fields produce an empty row
    (source_file column is always populated).
    """
    path = Path(path).resolve()
    md_paths = resolve_md_paths(path)
    if not md_paths:
        click.echo(f"No .md files found under {path}", err=True)
        sys.exit(1)

    config = load_form_config(Path(config_path))
    if not config.fields:
        click.echo(f"No fields defined in {config_path}. Add field entries and try again.", err=True)
        sys.exit(1)

    rows = [extract_row(md, config) for md in md_paths]

    out_path = (
        Path(output).resolve()
        if output
        else path.parent / f"{path.name}_forms.csv"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    headers = list(rows[0].keys())
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

    click.echo(f"{len(rows)} forms → {out_path}")


if __name__ == "__main__":
    main()
