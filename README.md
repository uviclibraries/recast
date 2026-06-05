# recast

Convert VLM/OCR tabular output into usable formats. Takes the **HTML** markup that a model like
[Chandra OCR](https://github.com/datalab-to/chandra) produces from a table or form image,
and converts it to CSV (or TSV, or Markdown).

Two tools:
- **`csvify`** — tables embedded in VLM output → one CSV with one row per table row
- **`formify`** — form fields in VLM output → one CSV with one row per form/file

---

## csvify

### First time with a new document type

Every document type needs a config that tells csvify what the columns are called.
Check if one already exists:

```bash
csvify --list-configs
```

If your document type isn't listed, create a config (see [Config reference](#config-reference) below), then run:

```bash
csvify path/to/output/ -c your_config_name
```

### If a config already exists

The config is selected automatically if the output folder name contains a matching pattern.
Otherwise, pass it explicitly with `-c`:

```bash
# Auto-detected from folder name
csvify path/to/output/

# Explicit config
csvify path/to/output/ -c your_config_name

# Specify output path
csvify path/to/output/ -c your_config_name -o results/output.csv
```

### Input path formats

`csvify` expects the folder layout that Chandra-OCR and similar models produce by default:

```
output/
  document_name/
    document_name.md    ← csvify reads this
```

Pass either the inner folder (`output/document_name/`) or the outer folder (`output/`) to
convert everything inside it at once.

---

## Config reference

Configs live in `configs/*.yaml`. Create one per document type.

```yaml
name: my_document_type
filename_patterns:
  - "keyword_in_filename"   # substring match — used for auto-detection

columns:
  - column_one      # CSV headers, in order, matching the table columns left to right
  - column_two
  - column_three
```

**For tables where multiple pages repeat the same schema** (e.g. one table per context or
site, all with the same columns), add:

```yaml
combine_all_tables: true
```

This concatenates rows from all matching tables in the file and skips tables with a
different column structure.

**For tables that use `"` or similar ditto marks** (same value as the row above):

```yaml
expand_dittos: true
```

---

## formify

For VLM output from **forms** rather than tables — where each file represents one record
(a survey sheet, intake form, catalogue card) and should become one row in the output. This handles both HTML and Markdown outputs from the VLM.

### Workflow

**Step 1 — generate a draft config (run once per form type)**

```bash
formify inspect path/to/output/ -o draft_config.yaml --name my_form_type
```

This scans all `.md` files in the directory, finds every field label (e.g. `**Station:**`,
`<b>Date:</b>`), and writes a draft config with counts showing how often each label appeared.

Open the draft and:
- Rename `column` values to whatever you want as CSV headers
- Delete fields you don't need
- Leave counts as comments — they show which fields are reliable across files

**Step 2 — convert**

```bash
formify convert path/to/output/ -c draft_config.yaml -o output.csv
```

One row per `.md` or `html` file. A `source_file` column is always included for traceability.
Fields not found in a given file are left empty — that's expected if a form has multiple
sides or optional sections.

### formify config format

```yaml
name: my_form_type
filename_patterns:
  - ""   # optional — leave blank and always pass -c explicitly

fields:
  - label: "Station"        # exactly as it appears in the VLM output
    column: station         # what you want the CSV column called
  - label: "Date"
    column: date
  - label: "Observer"
    column: observer
```

---

## Delivery notes

**HTML is the best intermediate format for tables.** When running a tabular job, always
ask the VLM for HTML output (not Markdown). HTML handles nested or merged columns and can
be converted to CSV, TSV, or Markdown without re-running the model. If a researcher asks
for a different format after the fact, feed the HTML file back into `csvify`.

**TOTAL rows:** Some source documents include subtotal rows at the end of each group
(common in archaeological tables, registers, etc.). These are included in the CSV as-is.
If the researcher sums a column across the whole file, those rows will cause double-counting.
Flag this at delivery and suggest they filter out summary rows before running totals
(e.g. exclude rows where the first column equals `"TOTAL"`).
