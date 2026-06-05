"""Load and resolve configs by filename pattern."""

from dataclasses import dataclass
from pathlib import Path
import yaml


@dataclass
class CsvConfig:
    """Configuration for CSV conversion."""

    name: str
    columns: list[str]
    filename_patterns: list[str]
    ditto_char: str | None = None  # Character used for "same as above" (e.g. ")
    table_index: int = 0  # Which table to use (0 = first); use -1 for auto (repeat-image table)
    metadata_sections: list[dict] | None = None  # [{table_index, columns}] - merged into each row
    # If set, run a dedicated code path (e.g. prose "back of form" with no HTML tables).
    processor: str | None = None
    # MLP survey front: merge station/weather/camera metadata into each data row (see converter).
    mlp_merge_front_metadata: bool = False
    # Lower = matched first when multiple configs share a path (see find_config_for_filename).
    match_priority: int = 100
    # When True, concatenate rows from ALL tables in the file (for repeated same-schema tables).
    combine_all_tables: bool = False


def load_config(path: Path) -> CsvConfig:
    """Load a single config file."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    ditto = data.get("ditto_char")
    if ditto is None and data.get("expand_dittos"):
        ditto = '"'  # Common ditto mark
    return CsvConfig(
        name=data["name"],
        columns=data["columns"],
        filename_patterns=data.get("filename_patterns", []),
        ditto_char=ditto,
        table_index=data.get("table_index", 0),
        metadata_sections=data.get("metadata_sections"),
        processor=data.get("processor"),
        mlp_merge_front_metadata=data.get("mlp_merge_front_metadata", False),
        match_priority=data.get("match_priority", 100),
        combine_all_tables=data.get("combine_all_tables", False),
    )


def load_all_configs(configs_dir: Path) -> list[CsvConfig]:
    """Load all YAML configs from a directory."""
    configs = []
    for path in configs_dir.glob("*.yaml"):
        try:
            configs.append(load_config(path))
        except Exception as e:
            print(f"Warning: Could not load config {path}: {e}")
    return configs


def match_config(config: CsvConfig, filename: str) -> bool:
    """Check if filename matches any of the config's patterns."""
    fn_lower = filename.lower()
    for pattern in config.filename_patterns:
        if pattern.lower() in fn_lower:
            return True
    return False


# Configs dir: project root (tabular-to-csv/configs/)
CONFIGS_DIR = Path(__file__).resolve().parent.parent / "configs"


def find_config_for_filename(name: str, configs_dir: Path | None = None) -> CsvConfig | None:
    """
    Find a config whose filename_patterns match the given name.
    Use the chandra output folder/filename (e.g. fireinsurance_victoria_1895_p06b).
    """
    configs_dir = configs_dir or CONFIGS_DIR
    configs = load_all_configs(configs_dir)
    configs.sort(key=lambda c: (c.match_priority, c.name))
    for config in configs:
        if match_config(config, name):
            return config
    return None


def find_config_for_path(path: Path, configs_dir: Path | None = None) -> CsvConfig | None:
    """
    Find a config that matches the given path.
    Includes the resolved path string so patterns can match dataset folders (e.g. …/MLP/…).
    """
    configs_dir = configs_dir or CONFIGS_DIR
    path = path.resolve()
    if path.is_file():
        match_str = f"{path.parent.name} {path.stem} {path.as_posix()}"
    else:
        match_str = f"{path.name} {path.as_posix()}"
    return find_config_for_filename(match_str, configs_dir)


def get_config_by_name(name: str, configs_dir: Path | None = None) -> CsvConfig | None:
    """Load config by name (e.g. fireinsurance_1895)."""
    configs_dir = configs_dir or CONFIGS_DIR
    config_path = configs_dir / f"{name}.yaml"
    if config_path.exists():
        return load_config(config_path)
    return None


def get_config(
    config_name: str | None,
    path_or_name: Path | str,
    configs_dir: Path | None = None,
) -> CsvConfig | None:
    """
    Get config: by name if specified, else by filename match.
    """
    configs_dir = configs_dir or CONFIGS_DIR
    if config_name:
        return get_config_by_name(config_name, configs_dir)

    if isinstance(path_or_name, Path):
        return find_config_for_path(path_or_name, configs_dir)
    return find_config_for_filename(path_or_name, configs_dir)


def list_configs(configs_dir: Path | None = None) -> list[CsvConfig]:
    """Return all loaded configs."""
    configs_dir = configs_dir or CONFIGS_DIR
    return load_all_configs(configs_dir)
