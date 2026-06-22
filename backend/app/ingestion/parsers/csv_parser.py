"""
CSV parser — handles the most common CPG data export formats.

Supports:
  - Standard comma-separated
  - Semicolon-separated (European locales)
  - Tab-separated (TSV)
  - Mixed quoting styles
  - UTF-8, latin-1, cp1252 encodings (auto-detected)
  - Header normalisation (strip whitespace, lowercase)
  - Numeric columns with thousands separators and currency symbols
"""

import io
import re
from typing import Optional

import pandas as pd

from app.core.logging import get_logger

log = get_logger(__name__)

ENCODINGS_TO_TRY = ["utf-8", "utf-8-sig", "latin-1", "cp1252"]
SEPARATORS_TO_TRY = [",", ";", "\t", "|"]


def parse_csv(
    content: bytes,
    source_name: str,
    max_rows: int = 100_000,
) -> list[dict]:
    """
    Parse CSV bytes into a list of raw dicts.
    Column names are lowercased and whitespace-stripped.
    Values are strings — type coercion happens in the validation layer.
    """
    df = _read_with_fallback(content, source_name)

    if len(df) > max_rows:
        log.warning("csv.truncated", source=source_name, rows=len(df), limit=max_rows)
        df = df.head(max_rows)

    # Normalise column names
    df.columns = [_normalise_col(c) for c in df.columns]

    # Drop fully empty rows
    df.dropna(how="all", inplace=True)

    # Stringify values, replace NaN/None with None
    records = []
    for _, row in df.iterrows():
        record: dict = {}
        for col, val in row.items():
            if pd.isna(val):
                record[col] = None
            else:
                record[col] = str(val).strip() if str(val).strip() != "" else None
        records.append(record)

    log.info("csv.parsed", source=source_name, rows=len(records))
    return records


def _read_with_fallback(content: bytes, source_name: str) -> pd.DataFrame:
    """Try multiple encodings and separators until one succeeds."""
    last_error: Optional[Exception] = None

    for encoding in ENCODINGS_TO_TRY:
        for sep in SEPARATORS_TO_TRY:
            try:
                df = pd.read_csv(
                    io.BytesIO(content),
                    sep=sep,
                    encoding=encoding,
                    dtype=str,
                    keep_default_na=False,
                    on_bad_lines="warn",
                )
                if df.shape[1] < 2:
                    # Likely wrong separator — try next
                    continue
                log.debug(
                    "csv.read_success",
                    source=source_name,
                    encoding=encoding,
                    sep=repr(sep),
                    rows=len(df),
                    cols=list(df.columns),
                )
                return df
            except Exception as e:
                last_error = e
                continue

    raise ValueError(
        f"Could not parse CSV from '{source_name}' with any known "
        f"encoding/separator combination. Last error: {last_error}"
    )


def _normalise_col(name: str) -> str:
    """Lowercase, strip, replace spaces/dashes with underscores."""
    return re.sub(r"[\s\-\.]+", "_", str(name).strip().lower()).strip("_")


def detect_delimiter(content: bytes) -> str:
    """Quick heuristic to detect the most likely delimiter."""
    sample = content[:2048].decode("utf-8", errors="replace")
    counts = {sep: sample.count(sep) for sep in SEPARATORS_TO_TRY}
    return max(counts, key=counts.get)
