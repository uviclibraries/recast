"""Simple CLI for converting VLM tabular output (MD/HTML) to CSV."""

from pathlib import Path

import click

from recast.config import get_config, list_configs
from recast.converter import convert_file


@click.command()
@click.argument(
    "path",
    type=click.Path(path_type=Path),
    required=False,
)
@click.option(
    "--config",
    "-c",
    type=str,
    default=None,
    help="Config name (e.g. fireinsurance_1895). Auto-detected from filename if omitted.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output path. Default: CSV next to the input.",
)
@click.option(
    "--list-configs",
    "list_configs_flag",
    is_flag=True,
    help="List available configs and exit.",
)
def main(path: Path | None, config: str | None, output: Path | None, list_configs_flag: bool):
    """Convert VLM tabular output (MD/HTML) to CSV with configured column headers.

    PATH: output folder containing a .md or .html file, or a folder of multiple outputs.

    Config is auto-selected from the filename when possible.
    """
    if list_configs_flag:
        click.echo("Available configs:")
        for cfg in list_configs():
            pats = ", ".join(cfg.filename_patterns[:3])
            if len(cfg.filename_patterns) > 3:
                pats += "..."
            click.echo(f"  {cfg.name}: columns={cfg.columns}, matches={pats}")
        return

    if not path:
        click.echo("Error: PATH is required. Use csvify --list-configs to see available configs.", err=True)
        raise SystemExit(1)

    path = Path(path).resolve()
    if not path.exists():
        click.echo(f"Error: {path} does not exist.", err=True)
        raise SystemExit(1)

    targets = _resolve_targets(path)
    if not targets:
        click.echo(f"No tabular output found under {path}", err=True)
        raise SystemExit(1)

    for target_md, target_name in targets:
        cfg = get_config(config, target_md)
        if cfg is None:
            click.echo(
                f"No config matches '{target_name}'. Use -c to pick one.",
                err=True,
            )
            raise SystemExit(1)

        if output:
            output = Path(output).resolve()
            if len(targets) == 1 and str(output).lower().endswith(".csv"):
                out_path = output
            else:
                out_path = output / f"{target_name}.csv"
                out_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            out_path = target_md.with_suffix(".csv")

        out_path = Path(out_path).resolve()
        try:
            n = convert_file(target_md, out_path, cfg)
            click.echo(f"  {target_name} → {out_path} ({n} rows)")
        except Exception as e:
            click.echo(f"  Error: {e}", err=True)
            raise SystemExit(1)


def _resolve_targets(path: Path) -> list[tuple[Path, str]]:
    """Return list of (md_path, name) for tabular output(s)."""
    path = path.resolve()
    results = []

    if path.is_file():
        if path.suffix.lower() == ".md":
            results.append((path, path.stem))
        return results

    # Look for subdirs with matching name/name.md pattern
    for item in sorted(path.iterdir()):
        if not item.is_dir():
            continue
        md_file = item / f"{item.name}.md"
        if md_file.exists():
            results.append((md_file, item.name))

    # Or path itself is the output dir (name/name.md)
    if not results:
        md_file = path / f"{path.name}.md"
        if md_file.exists():
            results.append((md_file, path.name))

    return results


if __name__ == "__main__":
    main()
