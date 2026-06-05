"""Convert markdown/HTML tables to CSV."""

# Layout-oriented OCR output (e.g. Chandra / Chandra-2) often embeds HTML <table> in .md;
# Markdown pipe tables are separate. See README "Input formats".

import csv
import re
from dataclasses import replace
from pathlib import Path
from typing import Literal

from recast.config import CsvConfig

# Column sets for processor=mlp_auto (side chosen from Chandra field labels; see classify_mlp_side).
# Patterns mirror Mountain Legacy repeat-photo field sheets (Chandra-2 / chandra HTML-in-markdown).
_MLP_FRONT_COLUMNS = (
    "location",
    "original_photo_no",
    "repeat_photo_no",
    "azimuth",
)
_MLP_BACK_COLUMNS = (
    "source_sheet",
    "weather_narrative",
    "weather_note",
    "station_keywords",
    "location_label",
    "location_photo_from",
    "location_photo_to",
    "location_narrative",
)


def parse_markdown_tables(content: str) -> list[list[str]]:
    """
    Extract tables from markdown. Returns list of tables, each table is list of rows.
    Handles markdown table format:
    | col1 | col2 |
    | --- | --- |
    | val1 | val2 |
    """
    tables = []
    lines = content.split("\n")
    in_table = False
    current_table = []
    separator_re = re.compile(r"^\|[\s\-:|]+\|$")

    for line in lines:
        if "|" in line and line.strip().startswith("|"):
            if not in_table:
                in_table = True
                current_table = []
            # Skip separator row
            if separator_re.match(line.strip()):
                continue
            # Parse row
            cells = [c.strip() for c in line.strip().split("|")[1:-1]]
            if cells:
                current_table.append(cells)
        else:
            if in_table and current_table:
                tables.append(current_table)
            in_table = False

    if in_table and current_table:
        tables.append(current_table)
    return tables


def extract_tables(content: str) -> list[list[list[str]]]:
    """Prefer HTML tables when present (typical Chandra .md); else GitHub-style pipe tables."""
    if "<table" in content.lower():
        return parse_html_tables(content)
    return parse_markdown_tables(content)


def parse_html_tables(content: str) -> list[list[list[str]]]:
    """Extract tables from HTML. Returns list of tables (list of rows, each row is list of cells)."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    soup = BeautifulSoup(content, "html.parser")
    tables = []
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = []
            for td in tr.find_all(["td", "th"]):
                cells.append(td.get_text(strip=True))
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def _extract_value(cell: str) -> str:
    """Extract value from '**Label:** value' or 'Label: value' format."""
    cell = cell.strip()
    if ": " in cell:
        return cell.split(": ", 1)[-1].strip()
    return cell


def _mlp_strip_field_label(cell: str) -> str:
    """Chandra often emits 'LABEL:value' without a space after the colon (HTML get_text)."""
    cell = cell.strip()
    if ":" not in cell:
        return cell
    return cell.split(":", 1)[-1].strip()


def _flatten_metadata_table(table: list[list[str]], columns: list[str]) -> dict[str, str]:
    """Flatten a table into a single row of key-value pairs. Cells read left-to-right, top-to-bottom."""
    cells = []
    for row in table:
        for cell in row:
            cells.append(_extract_value(cell))
    result = {}
    for i, col in enumerate(columns):
        result[col] = cells[i] if i < len(cells) else ""
    return result


def expand_ditto(row: list[str], prev_row: list[str], ditto_char: str) -> list[str]:
    """Replace ditto characters with the value from the row above."""
    result = []
    for i, cell in enumerate(row):
        if ditto_char and cell.strip() == ditto_char and i < len(prev_row):
            result.append(prev_row[i])
        else:
            result.append(cell)
    return result


def find_mlp_repeat_image_table_index(tables: list[list[list[str]]]) -> int | None:
    """Locate the Repeat Image Data grid (header row mentions Location + Original + Repeat)."""
    for i, t in enumerate(tables):
        for row in t[:6]:
            if len(row) < 3:
                continue
            joined = " ".join(row).lower()
            if (
                "location" in joined
                and "original" in joined
                and "repeat" in joined
            ):
                return i
    return None


def normalize_mlp_repeat_image_grid(table: list[list[str]]) -> list[list[str]]:
    """
    Expand HTML rows where <td rowspan> omitted the Location cell: rows may have 3 cells.
    Carries the current location from the last 4-column row.
    If there is no Location/Original header row (non-MLP tables), returns *table* unchanged.
    """
    start: int | None = None
    for ri, row in enumerate(table):
        cells = [c.strip() for c in row]
        if len(cells) >= 4 and cells[0].lower().startswith("location"):
            start = ri + 1
            break
    if start is None:
        return table
    out: list[list[str]] = []
    loc = ""
    for row in table[start:]:
        r = [c.strip() for c in row]
        if len(r) == 4:
            loc = r[0]
            out.append(r)
        elif len(r) == 3:
            if r[0] == "":
                out.append([loc, "", r[1], r[2]])
            else:
                out.append([loc, r[0], r[1], r[2]])
        elif len(r) == 2:
            out.append([loc, r[0], r[1], ""])
        elif len(r) == 1 and r[0]:
            out.append([loc, r[0], "", ""])
    return out


def _is_mlp_html_admin_table(table: list[list[str]]) -> bool:
    """True when table 0 is the typed header grid (not the repeat-image table only)."""
    if not table:
        return False
    blob = " ".join(" ".join(r) for r in table[:4]).upper()
    return "HISTORICAL" in blob or ("SURVEY" in blob and "STATION" in blob)


def build_mlp_front_metadata_row(tables: list[list[list[str]]]) -> dict[str, str]:
    """
    Flatten the first tables on the MLP survey *front* (admin, weather, camera rows).
    Cell text comes from Chandra HTML-as-markdown (bs4 get_text).
    """
    meta: dict[str, str] = {}
    if not tables:
        return meta
    t0 = tables[0]
    try:
        if len(t0) > 0 and len(t0[0]) >= 2:
            meta["historical_survey"] = _mlp_strip_field_label(t0[0][0])
            meta["year"] = _mlp_strip_field_label(t0[0][1])
        if len(t0) > 1 and t0[1]:
            meta["station"] = _mlp_strip_field_label(t0[1][0])
        if len(t0) > 2 and t0[2]:
            meta["repeat_date"] = _mlp_strip_field_label(t0[2][0])
        if len(t0) > 3 and len(t0[3]) >= 2:
            meta["start_time"] = _mlp_strip_field_label(t0[3][0])
            meta["finish_time"] = _mlp_strip_field_label(t0[3][1])
        if len(t0) > 4 and len(t0[4]) >= 2:
            meta["survey_map_sheet"] = _mlp_strip_field_label(t0[4][0])
            meta["nts_map_sheet"] = _mlp_strip_field_label(t0[4][1])
        if len(t0) > 5 and len(t0[5]) >= 2:
            meta["lat"] = _mlp_strip_field_label(t0[5][0])
            meta["long"] = _mlp_strip_field_label(t0[5][1])
        if len(t0) > 6 and t0[6]:
            meta["lone2_coordinate_note"] = t0[6][0].strip()
        if len(t0) > 7 and t0[7]:
            meta["hiking_party"] = _mlp_strip_field_label(t0[7][0])
        if len(t0) > 8 and len(t0[8]) >= 2:
            meta["pilot"] = _mlp_strip_field_label(t0[8][0])
            meta["rw_call_sign"] = _mlp_strip_field_label(t0[8][1])
    except (IndexError, TypeError):
        pass

    if len(tables) > 1:
        w = tables[1]
        if len(w) > 1 and len(w[1]) >= 6:
            meta["avg_wind_speed_kmh"] = w[1][1].strip()
            meta["max_gust_speed_kmh"] = w[1][4].strip()
        if len(w) > 2 and len(w[2]) >= 6:
            meta["temperature_c"] = w[2][1].strip()
            meta["relative_humidity_pct"] = w[2][4].strip()
        if len(w) > 3 and len(w[3]) >= 6:
            meta["barometric_pressure_hpa"] = w[3][1].strip()
            meta["wet_bulb_c"] = w[3][4].strip()

    if len(tables) > 2:
        cam = tables[2]
        if len(cam) > 0:
            meta["camera_row0"] = " | ".join(cam[0])
        if len(cam) > 1:
            meta["camera_row1"] = " | ".join(cam[1])

    return meta


def classify_mlp_side(content: str) -> Literal["front", "back"]:
    """
    Distinguish MLP form sides using recurring Chandra field labels, not image filenames.
    Front: typed header + Repeat Image Data grid (HTML or bold block labels).
    Back: Rite-in-the-Rain WEATHER NARRATIVE / STATION KEYWORDS / LOCATION NARRATIVE blocks.
    """
    c = content
    low = c.lower()
    back_score = 0
    if re.search(r"\*\*WEATHER NARRATIVE:\*\*", c, re.IGNORECASE):
        back_score += 4
    if re.search(r"\*\*STATION KEYWORDS:\*\*", c, re.IGNORECASE):
        back_score += 4
    if re.search(r"\*\*LOCATION NARRATIVE:\*\*", c, re.IGNORECASE):
        back_score += 2

    front_score = 0
    if "<table" in low and "historical" in low and "survey" in low:
        front_score += 4
    if re.search(r"\*\*HISTORICAL SURVEY:\*\*", c, re.IGNORECASE):
        front_score += 3
    if "repeat image data" in low:
        front_score += 3
    if "<table" in low and "repeat image" in low:
        front_score += 2
    # Repeat grid headers (Chandra often preserves th text)
    if re.search(
        r"original\s+photo|repeat\s+photo", low
    ) and "location" in low and "<table" in low:
        front_score += 2

    if back_score >= 4 and back_score >= front_score:
        return "back"
    if front_score > 0:
        return "front"
    if back_score > 0:
        return "back"
    # Single-sided or odd OCR: prefer front path if an HTML repeat grid exists
    if "<table" in low and find_mlp_repeat_image_table_index(parse_html_tables(c)) is not None:
        return "front"
    return "back"


def mlp_back_preamble(content: str) -> dict[str, str]:
    """Top-of-form prose on the MLP Rite-in-the-Rain back (before LOCATION blocks)."""
    meta: dict[str, str] = {}
    wn = re.search(
        r"\*\*WEATHER NARRATIVE:\*\*\s*(.*?)(?=\n\s*\\?\*\s*NOTE:|\*\*STATION KEYWORDS:|\Z)",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if wn:
        meta["weather_narrative"] = re.sub(r"\s+", " ", wn.group(1).strip())
    note = re.search(
        r"((?:\\)?\*\s*NOTE:.*?)(?=\*\*STATION KEYWORDS:|\*\*LOCATION:|\Z)",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if note:
        note_txt = re.sub(r"\s+", " ", note.group(1).strip())
        meta["weather_note"] = note_txt.replace("\\*", "*")
    sk = re.search(
        r"\*\*STATION KEYWORDS:\*\*\s*(.*?)(?=\*\*LOCATION:|\Z)",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if sk:
        meta["station_keywords"] = re.sub(r"\s+", " ", sk.group(1).strip())
    return meta


# Chandra sometimes omits the empty label: `**LOCATION:**` vs slot label `**LOCATION: 1**`.
_RX_LOC_BLOCKS = re.compile(
    r"(?:"
    r"\*\*LOCATION:\*\*\s*(?P<h_std>.*?)\s*\*\*LOCATION NARRATIVE:\*\*\s*(?P<n_std>.*?)"
    r"|"
    r"\*\*LOCATION:\s*(?P<lid>[^*\n]+)\*\*\s*(?P<h_lid>.*?)\s*\*\*LOCATION NARRATIVE:\*\*\s*(?P<n_lid>.*?)"
    r")"
    r"(?=\*\*LOCATION:|\Z)",
    re.DOTALL | re.IGNORECASE,
)


def parse_mlp_back_side_markdown(content: str) -> list[dict[str, str]]:
    """
    Parse the Rite-in-the-Rain *back* side: bold LOCATION / LOCATION NARRATIVE blocks.
    Blank blocks (unused slots on the form) are skipped.
    """
    rows_out: list[dict[str, str]] = []
    for m in _RX_LOC_BLOCKS.finditer(content):
        if m.group("h_std") is not None:
            hdr = re.sub(r"\s+", " ", m.group("h_std").strip())
            narr = (m.group("n_std") or "").strip()
        else:
            lid = (m.group("lid") or "").strip()
            rest = re.sub(r"\s+", " ", (m.group("h_lid") or "").strip())
            hdr = f"{lid} {rest}".strip() if lid else rest
            narr = (m.group("n_lid") or "").strip()
        photo_m = re.search(
            r"Location Photo No\.'?s:\s*(\d+)\s+to\s+(\d+)", hdr, re.IGNORECASE
        )
        photo_from, photo_to = "", ""
        if photo_m:
            photo_from, photo_to = photo_m.group(1), photo_m.group(2)
            loc_label = hdr[: photo_m.start()].strip()
        else:
            loose = re.search(r"(\d+)\s+to\s+(\d+)", hdr)
            if loose:
                photo_from, photo_to = loose.group(1), loose.group(2)
                loc_label = hdr[: loose.start()].strip()
            else:
                loc_label = hdr
        if not loc_label and not narr.strip():
            continue
        low = loc_label.lower()
        if (
            "location photo" in low
            and not photo_from
            and not photo_to
            and not narr.strip()
        ):
            continue
        if low.startswith("location photo") and not photo_from:
            continue
        rows_out.append(
            {
                "location_label": loc_label,
                "location_photo_from": photo_from,
                "location_photo_to": photo_to,
                "location_narrative": narr,
            }
        )
    return rows_out


def _is_header_row(row: list[str], columns: list[str]) -> bool:
    """True when all non-empty cells in row match config column names (case-insensitive)."""
    cols_upper = {c.upper().strip() for c in columns}
    non_empty = [c.strip() for c in row if c.strip()]
    if not non_empty:
        return False
    return all(c.upper() in cols_upper for c in non_empty)


def _is_blank_csv_row(row: dict[str, str], columns: list[str]) -> bool:
    """True when every mapped column is empty or NBSP-only (common Chandra/HTML padding)."""
    for col in columns:
        v = (row.get(col) or "").replace("\u00a0", " ").strip()
        if v:
            return False
    return True


def rows_to_csv_rows(
    table: list[list[str]], config: CsvConfig
) -> list[dict[str, str]]:
    """
    Convert table rows to dicts keyed by config columns.
    First row of table is treated as headers unless config has columns.
    """
    if not table:
        return []

    if config.columns:
        headers = config.columns
        # Table may have different headers - map by position or use first row
        # Strategy: if table has header row, map by index; else assume first row is data
        data_start = 0
        if table[0] and not any(
            h.lower() in str(table[0][0]).lower() for h in config.columns
        ):
            # First row might be headers - check if second row looks like data
            data_start = 0
        # Map: use first N columns of each row to config columns
        rows_dict = []
        prev_row = [""] * len(headers)
        for row in table:
            expanded = (
                expand_ditto(row, prev_row, config.ditto_char)
                if config.ditto_char and prev_row[0]
                else row
            )
            # Take first len(headers) cells, pad if needed
            values = []
            for i, col in enumerate(headers):
                if i < len(expanded):
                    values.append(expanded[i].strip())
                else:
                    values.append("")
            row_dict = dict(zip(headers, values))
            rows_dict.append(row_dict)
            prev_row = values
        return rows_dict

    # No config columns - use first row as headers
    headers = table[0]
    return [dict(zip(headers, row)) for row in table[1:] if len(row) >= len(headers)]


def convert_file(
    input_path: Path,
    output_path: Path,
    config: CsvConfig,
    prefer_md: bool = True,
) -> int:
    """
    Convert a chandra output file to CSV.
    Returns number of rows written.
    """
    md_path = input_path.with_suffix(".md")
    html_path = input_path.with_suffix(".html")

    if prefer_md and md_path.exists():
        content = md_path.read_text(encoding="utf-8")
    elif html_path.exists():
        content = html_path.read_text(encoding="utf-8")
    elif input_path.suffix == ".md" and input_path.exists():
        content = input_path.read_text(encoding="utf-8")
    elif input_path.suffix == ".html" and input_path.exists():
        content = input_path.read_text(encoding="utf-8")
    else:
        raise FileNotFoundError(f"No .md or .html file found for {input_path}")

    if getattr(config, "processor", None) == "mlp_auto":
        side = classify_mlp_side(content)
        if side == "back":
            config = replace(
                config,
                processor="mlp_back",
                columns=list(_MLP_BACK_COLUMNS),
                mlp_merge_front_metadata=False,
            )
        else:
            config = replace(
                config,
                processor=None,
                columns=list(_MLP_FRONT_COLUMNS),
                mlp_merge_front_metadata=True,
                table_index=-1,
            )

    if getattr(config, "processor", None) == "mlp_back":
        rows_out = parse_mlp_back_side_markdown(content)
        if not rows_out:
            return 0
        preamble = mlp_back_preamble(content)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        headers = list(config.columns)
        id_stem = input_path.parent.name if input_path.parent.name else input_path.stem
        with open(output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for r in rows_out:
                row = {**preamble, "source_sheet": id_stem, **r}
                writer.writerow({k: row.get(k, "") for k in headers})
        return len(rows_out)

    tables = extract_tables(content)

    if not tables:
        return 0

    if getattr(config, "combine_all_tables", False):
        all_rows: list[dict[str, str]] = []
        for table in tables:
            if not table:
                continue
            if config.columns:
                if not _is_header_row(table[0], config.columns):
                    continue  # different schema — skip this table
                table = table[1:]  # strip the matched header row
            rows = rows_to_csv_rows(table, config)
            if config.columns:
                rows = [r for r in rows if not _is_blank_csv_row(r, config.columns)]
            all_rows.extend(rows)
        if not all_rows:
            return 0
        headers = list(all_rows[0].keys())
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(all_rows)
        return len(all_rows)

    idx: int = config.table_index
    if idx < 0:
        found = find_mlp_repeat_image_table_index(tables)
        idx = found if found is not None else 0
    if idx >= len(tables):
        idx = 0
    raw_table = tables[idx]
    table = normalize_mlp_repeat_image_grid(raw_table)

    if getattr(config, "mlp_merge_front_metadata", False):
        rows_dict = rows_to_csv_rows(table, config)
        meta_row: dict[str, str] = {}
        if tables and _is_mlp_html_admin_table(tables[0]):
            meta_row = build_mlp_front_metadata_row(tables)
        if meta_row and rows_dict:
            rows_dict = [{**meta_row, **r} for r in rows_dict]
    else:
        rows_dict = rows_to_csv_rows(table, config)

    if config.columns:
        rows_dict = [r for r in rows_dict if not _is_blank_csv_row(r, config.columns)]

    if not rows_dict:
        return 0

    # Merge metadata from additional tables into each row (legacy fire-insurance style).
    metadata_sections = getattr(config, "metadata_sections", None) or []
    metadata_row: dict[str, str] = {}
    if not getattr(config, "mlp_merge_front_metadata", False):
        for section in metadata_sections:
            ti = section.get("table_index", 0)
            cols = section.get("columns", [])
            if ti < len(tables) and cols:
                metadata_row.update(_flatten_metadata_table(tables[ti], cols))

    if metadata_row:
        headers = list(metadata_row.keys()) + list(rows_dict[0].keys())
        rows_dict = [{**metadata_row, **r} for r in rows_dict]
    else:
        headers = list(rows_dict[0].keys())
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows_dict)

    return len(rows_dict)


def convert_chandra_output(
    input_path: Path,
    output_path: Path | None,
    config: CsvConfig,
) -> Path:
    """
    Convert chandra output directory or file to CSV.
    Chandra layout: output_dir / doc_name / doc_name.md
    """
    input_path = Path(input_path).resolve()

    if input_path.is_file():
        # Single file
        in_path = input_path
        stem = input_path.stem
    elif input_path.is_dir():
        # Directory: find .md file (same name as dir or first found)
        stem = input_path.name
        md = input_path / f"{stem}.md"
        if md.exists():
            in_path = md
        else:
            md_files = list(input_path.glob("*.md"))
            if not md_files:
                raise FileNotFoundError(f"No .md file in {input_path}")
            in_path = md_files[0]
            stem = in_path.stem
    else:
        raise FileNotFoundError(f"Path not found: {input_path}")

    if output_path is None:
        output_path = input_path.parent if input_path.is_file() else input_path
    output_path = Path(output_path).resolve()

    if output_path.suffix != ".csv":
        out_csv = output_path / f"{stem}.csv"
    else:
        out_csv = output_path

    convert_file(in_path, out_csv, config)
    return out_csv
