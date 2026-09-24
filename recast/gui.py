"""Gradio GUI for csvify and formify.

Launch with `recast-gui` (or `python -m recast.gui`). Covers the same ground as the CLIs:
  csvify:  convert tables in VLM output to CSV / TSV / Markdown, inspect tables,
           list / create / edit configs in configs/
  formify: scan forms for field labels (draft config), edit the config, convert
           one form per file → one row
"""

from __future__ import annotations

import csv
import shutil
import tempfile
import zipfile
from pathlib import Path

import click
import gradio as gr
import pandas as pd
import yaml

from recast.config import CONFIGS_DIR, get_config, list_configs, load_config
from recast.converter import convert_file, extract_tables
from recast.form_config import load_form_config, write_draft_config
from recast.form_extractor import extract_row, scan_all_labels

AUTO = "(auto-detect from filename)"
FORMATS = ["CSV", "TSV", "Markdown"]
_EXT = {"CSV": ".csv", "TSV": ".tsv", "Markdown": ".md"}
_INPUT_SUFFIXES = (".md", ".html")

NEW_CONFIG_TEMPLATE = """\
name: my_document_type
filename_patterns:
  - "keyword_in_filename"   # substring match — used for auto-detection

columns:
  - column_one      # CSV headers, in order, matching the table columns left to right
  - column_two
  - column_three

# combine_all_tables: true   # concatenate rows from every table with this header row
# expand_dittos: true        # replace " ditto marks with the value from the row above
# table_index: 0             # which table to use when not combining (0 = first)
"""


# ── Input resolution ──────────────────────────────────────────────────────────

def _find_output_file(folder: Path) -> Path | None:
    """Chandra layout: folder/folder.md (or .html)."""
    for suffix in _INPUT_SUFFIXES:
        f = folder / f"{folder.name}{suffix}"
        if f.exists():
            return f
    return None


def _resolve_inputs(path_text: str, uploads: list | None) -> list[tuple[Path, str]]:
    """Return [(file, name), ...] from uploaded files and/or a folder/file path on disk.

    Mirrors the CLIs: a single .md/.html file, an output folder (name/name.md), or a folder
    of output folders. Falls back to loose .md/.html files in the folder.
    """
    results: list[tuple[Path, str]] = []

    for up in uploads or []:
        p = Path(up if isinstance(up, str) else up.name)
        if p.suffix.lower() in _INPUT_SUFFIXES:
            results.append((p, p.stem))

    path_text = (path_text or "").strip()
    if path_text:
        path = Path(path_text).expanduser().resolve()
        if not path.exists():
            raise gr.Error(f"{path} does not exist.")
        if path.is_file():
            if path.suffix.lower() in _INPUT_SUFFIXES:
                results.append((path, path.stem))
        else:
            found = []
            for item in sorted(path.iterdir()):
                if item.is_dir() and (f := _find_output_file(item)):
                    found.append((f, item.name))
            if not found and (f := _find_output_file(path)):
                found.append((f, path.name))
            if not found:
                found = [
                    (f, f.stem)
                    for f in sorted(path.iterdir())
                    if f.is_file() and f.suffix.lower() in _INPUT_SUFFIXES
                ]
            results.extend(found)

    if not results:
        raise gr.Error("No .md or .html VLM output found. Upload files or enter a path.")
    return results


# ── Output writing ────────────────────────────────────────────────────────────

def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    return (rows[0], rows[1:]) if rows else ([], [])


def _md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _write_table(path: Path, headers: list[str], rows: list[list[str]], fmt: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "Markdown":
        lines = [
            "| " + " | ".join(_md_cell(h) for h in headers) + " |",
            "| " + " | ".join("---" for _ in headers) + " |",
        ]
        lines += ["| " + " | ".join(_md_cell(c) for c in r) + " |" for r in rows]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t" if fmt == "TSV" else ",")
        writer.writerow(headers)
        writer.writerows(rows)


def _finalize_outputs(
    outputs: dict[str, Path], save_dir: str
) -> tuple[list[str], list[str]]:
    """Copy outputs to save_dir (if given) and add a zip when there are several files.

    Returns (download file paths, log lines).
    """
    log: list[str] = []
    files = [str(p) for p in outputs.values()]
    if len(files) > 1:
        first = next(iter(outputs.values()))
        zip_path = first.parent / "recast_outputs.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for p in outputs.values():
                zf.write(p, p.name)
        files.insert(0, str(zip_path))

    save_dir = (save_dir or "").strip()
    if save_dir and outputs:
        dest = Path(save_dir).expanduser().resolve()
        dest.mkdir(parents=True, exist_ok=True)
        for p in outputs.values():
            shutil.copy2(p, dest / p.name)
        log.append(f"Saved {len(outputs)} file(s) to {dest}")
    return files, log


def _preview(results: dict, key: str | None) -> pd.DataFrame:
    if not results or key not in results:
        return pd.DataFrame()
    headers, rows = results[key]
    return pd.DataFrame(rows, columns=headers)


def _total_row_warning(name: str, rows: list[list[str]]) -> str | None:
    n = sum(1 for r in rows if r and r[0].strip().upper() == "TOTAL")
    if n:
        return (
            f"  ⚠ {name}: {n} row(s) with first column 'TOTAL' — summing a column "
            "across the file will double-count. Filter these before running totals."
        )
    return None


# ── csvify ────────────────────────────────────────────────────────────────────

def _config_choices() -> list[str]:
    return [AUTO] + sorted(c.name for c in list_configs())


def _configs_table() -> pd.DataFrame:
    rows = []
    for c in sorted(list_configs(), key=lambda c: c.name):
        flags = []
        if c.combine_all_tables:
            flags.append("combine_all_tables")
        if c.ditto_char:
            flags.append(f"dittos ({c.ditto_char})")
        if c.processor:
            flags.append(f"processor={c.processor}")
        if not c.combine_all_tables and not c.processor:
            flags.append(f"table_index={c.table_index}")
        rows.append([c.name, ", ".join(c.columns), ", ".join(c.filename_patterns), ", ".join(flags)])
    return pd.DataFrame(rows, columns=["name", "columns", "filename_patterns", "options"])


def csvify_inspect(path_text, uploads):
    """Show every table found in each input — helps pick columns / table_index for a new config."""
    rows = []
    for f, name in _resolve_inputs(path_text, uploads):
        content = f.read_text(encoding="utf-8", errors="replace")
        tables = extract_tables(content)
        if not tables:
            rows.append([name, "", 0, 0, "(no tables found)"])
        for i, t in enumerate(tables):
            width = max(len(r) for r in t)
            rows.append([name, i, len(t), width, " | ".join(t[0])])
    return pd.DataFrame(
        rows, columns=["file", "table_index", "rows", "columns", "first row"]
    )


def csvify_run(path_text, uploads, config_name, fmt, save_dir):
    targets = _resolve_inputs(path_text, uploads)
    work = Path(tempfile.mkdtemp(prefix="csvify_"))
    explicit = None if config_name in (None, "", AUTO) else config_name

    log: list[str] = []
    outputs: dict[str, Path] = {}
    results: dict[str, tuple[list[str], list[list[str]]]] = {}
    for f, name in targets:
        cfg = get_config(explicit, f)
        if cfg is None:
            log.append(f"  ✗ {name}: no config matches — pick one from the Config dropdown.")
            continue
        csv_path = work / "csv" / f"{name}.csv"
        try:
            n = convert_file(f, csv_path, cfg)
        except Exception as e:
            log.append(f"  ✗ {name}: {e}")
            continue
        if n == 0 or not csv_path.exists():
            log.append(f"  ✗ {name} [{cfg.name}]: 0 rows — no matching table found.")
            continue
        headers, rows = _read_csv(csv_path)
        out_path = work / "out" / f"{name}{_EXT[fmt]}"
        _write_table(out_path, headers, rows, fmt)
        outputs[name] = out_path
        results[name] = (headers, rows)
        log.append(f"  ✓ {name} [{cfg.name}] → {out_path.name} ({n} rows)")
        if warning := _total_row_warning(name, rows):
            log.append(warning)

    files, extra = _finalize_outputs(outputs, save_dir)
    log += extra
    keys = list(results)
    first = keys[0] if keys else None
    return (
        "\n".join(log),
        files or None,
        results,
        gr.update(choices=keys, value=first),
        _preview(results, first),
    )


def config_load(name):
    if not name or name == AUTO:
        return NEW_CONFIG_TEMPLATE
    path = CONFIGS_DIR / f"{name}.yaml"
    if not path.exists():
        raise gr.Error(f"{path.name} not found in {CONFIGS_DIR}")
    return path.read_text(encoding="utf-8")


def config_save(text, overwrite):
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise gr.Error(f"Invalid YAML: {e}")
    if not isinstance(data, dict) or not data.get("name"):
        raise gr.Error("Config needs a 'name'.")
    if not data.get("columns"):
        raise gr.Error("Config needs a list of 'columns'.")
    name = str(data["name"])
    if "/" in name or "\\" in name:
        raise gr.Error("Config name can't contain path separators.")
    path = CONFIGS_DIR / f"{name}.yaml"
    if path.exists() and not overwrite:
        raise gr.Error(f"{path.name} already exists. Tick 'Overwrite' to replace it.")
    tmp = Path(tempfile.mkdtemp()) / path.name
    tmp.write_text(text, encoding="utf-8")
    load_config(tmp)  # validate before touching configs/
    path.write_text(text, encoding="utf-8")
    choices = _config_choices()
    gr.Info(f"Saved {path}")
    return (
        _configs_table(),
        gr.update(choices=choices[1:], value=name),
        gr.update(choices=choices),
    )


# ── formify ───────────────────────────────────────────────────────────────────

def formify_inspect(path_text, uploads, name):
    files = [f for f, _ in _resolve_inputs(path_text, uploads)]
    field_counts, total = scan_all_labels(files)
    if not field_counts:
        raise gr.Error(
            "No **Label:** or <b>Label:</b> fields found. "
            "Check that VLM output uses bold field labels."
        )
    name = (name or "draft").strip() or "draft"
    draft = Path(tempfile.mkdtemp()) / f"{name}_config.yaml"
    write_draft_config(draft, name, field_counts, total)
    counts = pd.DataFrame(
        [[label, f"{n}/{total}"] for label, n in field_counts],
        columns=["label", "files"],
    )
    status = (
        f"Scanned {total} files — found {len(field_counts)} fields.\n"
        "Edit the draft below: rename 'column' values, delete fields you don't need."
    )
    return status, counts, draft.read_text(encoding="utf-8")


def formify_load_config(upload, path_text):
    src = upload if upload else (path_text or "").strip()
    if not src:
        raise gr.Error("Upload a config YAML or enter its path.")
    path = Path(src if isinstance(src, str) else src.name).expanduser()
    if not path.exists():
        raise gr.Error(f"{path} does not exist.")
    return path.read_text(encoding="utf-8")


def formify_save_config(text, path_text):
    path_text = (path_text or "").strip()
    if not path_text:
        raise gr.Error("Enter a path to save the config to.")
    _parse_form_config(text)
    path = Path(path_text).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    gr.Info(f"Saved {path}")


def _parse_form_config(text):
    try:
        yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise gr.Error(f"Invalid YAML: {e}")
    tmp = Path(tempfile.mkdtemp()) / "form_config.yaml"
    tmp.write_text(text or "", encoding="utf-8")
    try:
        config = load_form_config(tmp)
    except (KeyError, TypeError, AttributeError) as e:
        raise gr.Error(f"Config is malformed ({e}). Each field needs 'label' and 'column'.")
    if not config.fields:
        raise gr.Error("No fields defined in the config. Run 'Scan fields' or add field entries.")
    return config


def formify_run(path_text, uploads, config_text, fmt, out_name, save_dir):
    files = [f for f, _ in _resolve_inputs(path_text, uploads)]
    config = _parse_form_config(config_text)
    rows = [extract_row(f, config) for f in files]
    headers = list(rows[0].keys())
    data = [[r.get(h, "") for h in headers] for r in rows]

    default_stem = Path(path_text.strip()).name if (path_text or "").strip() else config.name
    stem = (out_name or "").strip() or f"{default_stem}_forms"
    out_path = Path(tempfile.mkdtemp(prefix="formify_")) / f"{stem}{_EXT[fmt]}"
    _write_table(out_path, headers, data, fmt)

    filled = sum(1 for r in rows for h in headers[1:] if r[h])
    possible = len(rows) * (len(headers) - 1)
    log = [f"{len(rows)} forms → {out_path.name} ({filled}/{possible} fields filled)"]
    download, extra = _finalize_outputs({stem: out_path}, save_dir)
    log += extra
    return "\n".join(log), download, pd.DataFrame(data, columns=headers)


# ── Layout ────────────────────────────────────────────────────────────────────

def _input_widgets(label: str):
    with gr.Row():
        path = gr.Textbox(
            label="Folder or file path on disk",
            placeholder="e.g. ~/projects/chandra/output/  or  output/doc_name/",
            info=label,
            scale=2,
        )
        uploads = gr.File(
            label="…or upload .md / .html files",
            file_count="multiple",
            file_types=list(_INPUT_SUFFIXES),
            scale=1,
        )
    return path, uploads


def build_app() -> gr.Blocks:
    with gr.Blocks(title="recast") as app:
        gr.Markdown(
            "# recast\n"
            "Convert VLM/OCR output (HTML or Markdown, e.g. from Chandra) into CSV, TSV "
            "or Markdown. **csvify** for tables (one row per table row), **formify** for "
            "forms (one row per file)."
        )

        with gr.Tab("csvify — tables"):
            c_path, c_uploads = _input_widgets(
                "An output folder (name/name.md), a folder of them, or a single file."
            )
            with gr.Row():
                c_config = gr.Dropdown(
                    _config_choices(), value=AUTO, label="Config",
                    info="Auto-detect matches filename_patterns against the file/folder name.",
                )
                c_fmt = gr.Radio(FORMATS, value="CSV", label="Output format")
                c_save = gr.Textbox(
                    label="Also save to folder (optional)",
                    placeholder="leave blank to just download",
                )
            with gr.Row():
                c_run = gr.Button("Convert", variant="primary")
                c_inspect = gr.Button("Inspect tables")
            c_log = gr.Textbox(label="Log", lines=4, interactive=False)
            c_files = gr.File(label="Download", file_count="multiple", interactive=False)
            c_results = gr.State({})
            c_pick = gr.Dropdown([], label="Preview output", interactive=True)
            c_table = gr.Dataframe(label="Preview", wrap=True, interactive=False)

            with gr.Accordion("Tables found in input", open=False) as c_inspect_box:
                gr.Markdown(
                    "Use this when creating a config for a new document type: it lists "
                    "every table with its index, size, and first row."
                )
                c_inspect_table = gr.Dataframe(wrap=True, interactive=False)

        with gr.Tab("csvify — configs"):
            gr.Markdown(
                f"Configs live in `{CONFIGS_DIR}`. One per document type — see the README's "
                "*Config reference* for all options."
            )
            cfg_table = gr.Dataframe(_configs_table(), wrap=True, interactive=False)
            with gr.Row():
                cfg_pick = gr.Dropdown(
                    _config_choices()[1:], label="Edit existing config", scale=2
                )
                cfg_new = gr.Button("New config", scale=1)
            cfg_text = gr.Code(NEW_CONFIG_TEMPLATE, language="yaml", label="Config YAML")
            with gr.Row():
                cfg_overwrite = gr.Checkbox(label="Overwrite if it exists", value=False)
                cfg_save = gr.Button("Save to configs/", variant="primary")

        with gr.Tab("formify — forms"):
            f_path, f_uploads = _input_widgets(
                "A folder of .md/.html files, or subfolders with name/name.md."
            )
            gr.Markdown("### Step 1 — build a config")
            with gr.Row():
                f_name = gr.Textbox(label="Config name", value="my_form_type")
                f_scan = gr.Button("Scan fields → draft config", variant="primary")
            with gr.Accordion("…or load an existing config", open=False):
                with gr.Row():
                    f_cfg_upload = gr.File(label="Config YAML", file_types=[".yaml", ".yml"])
                    f_cfg_path = gr.Textbox(label="…or path to config YAML")
                f_cfg_load = gr.Button("Load config")
            f_status = gr.Textbox(label="Status", lines=2, interactive=False)
            with gr.Row():
                f_counts = gr.Dataframe(
                    label="Fields found (files containing each label)",
                    interactive=False, scale=1,
                )
                f_cfg_text = gr.Code(language="yaml", label="Config YAML (editable)", scale=2)
            with gr.Row():
                f_cfg_save_path = gr.Textbox(
                    label="Save config to", placeholder="e.g. configs/my_form_type.yaml", scale=3
                )
                f_cfg_save = gr.Button("Save config", scale=1)

            gr.Markdown("### Step 2 — convert")
            with gr.Row():
                f_fmt = gr.Radio(FORMATS, value="CSV", label="Output format")
                f_out_name = gr.Textbox(label="Output filename (no extension)", placeholder="<name>_forms")
                f_save = gr.Textbox(
                    label="Also save to folder (optional)",
                    placeholder="leave blank to just download",
                )
            f_run = gr.Button("Convert", variant="primary")
            f_log = gr.Textbox(label="Log", lines=2, interactive=False)
            f_file = gr.File(label="Download", file_count="multiple", interactive=False)
            f_table = gr.Dataframe(label="Preview", wrap=True, interactive=False)

        # csvify
        c_run.click(
            csvify_run,
            [c_path, c_uploads, c_config, c_fmt, c_save],
            [c_log, c_files, c_results, c_pick, c_table],
        )
        c_pick.change(_preview, [c_results, c_pick], c_table)
        c_inspect.click(csvify_inspect, [c_path, c_uploads], c_inspect_table).then(
            lambda: gr.Accordion(open=True), None, c_inspect_box
        )

        # configs
        cfg_pick.input(config_load, cfg_pick, cfg_text)
        cfg_new.click(lambda: (NEW_CONFIG_TEMPLATE, None), None, [cfg_text, cfg_pick])
        cfg_save.click(config_save, [cfg_text, cfg_overwrite], [cfg_table, cfg_pick, c_config])

        # formify
        f_scan.click(formify_inspect, [f_path, f_uploads, f_name], [f_status, f_counts, f_cfg_text])
        f_cfg_load.click(formify_load_config, [f_cfg_upload, f_cfg_path], f_cfg_text)
        f_cfg_save.click(formify_save_config, [f_cfg_text, f_cfg_save_path], None)
        f_run.click(
            formify_run,
            [f_path, f_uploads, f_cfg_text, f_fmt, f_out_name, f_save],
            [f_log, f_file, f_table],
        )

    return app


@click.command()
@click.option("--host", default="127.0.0.1", help="Interface to bind (0.0.0.0 for LAN access).")
@click.option("--port", default=7860, type=int, help="Port to serve on.")
@click.option("--share", is_flag=True, help="Create a public gradio.live link.")
def main(host: str, port: int, share: bool) -> None:
    """Launch the recast GUI in your browser."""
    build_app().launch(server_name=host, server_port=port, share=share)


if __name__ == "__main__":
    main()
