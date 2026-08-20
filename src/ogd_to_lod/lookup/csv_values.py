"""Full-CSV distinct-value counting for candidate lookup columns.
"""

from __future__ import annotations

import csv
import io
import logging
from collections import Counter
from dataclasses import dataclass, field

from ogd_to_lod.parsers.csv_parser import CSVParseError, _detect_delimiter, _read_file_content

logger = logging.getLogger(__name__)

DEFAULT_MAX_DISTINCT_VALUES = 50_000


class CSVValuesError(Exception):
    """Raised when the full CSV value pass fails."""

    pass


@dataclass
class CSVValues:
    """Result of a full streaming pass over the candidate columns.

    `columns` maps column name -> Counter of raw string value -> row
    count. `total_rows` is the exact row count. `truncated` holds the
    names of columns whose distinct-value count hit
    `max_distinct_values`; for those columns any coverage computed
    downstream is a lower bound only.
    """

    columns: dict[str, Counter[str]] = field(default_factory=dict)
    total_rows: int = 0
    truncated: set[str] = field(default_factory=set)

    def as_tuple(self) -> tuple[dict[str, Counter[str]], int]:
        return self.columns, self.total_rows


def get_column_values(
        source: str,
        columns: list[str],
        encoding: str | None = None,
        delimiter: str | None = None,
        max_distinct_values: int = DEFAULT_MAX_DISTINCT_VALUES,
) -> CSVValues:
    """Stream the full CSV and count values per candidate column.

    Args:
        source: File path or URL (passed straight through to
            `_read_file_content`, so URL fetching + encoding detection
            are reused as-is).
        columns: Candidate column names to track.
        encoding: Optional forced encoding; auto-detected if None.
        delimiter: Optional forced delimiter; auto-detected if None.
        max_distinct_values: Cap on distinct values tracked per column

    Returns:
        A `CSVValues` with per-column `Counter`s, the exact total row
        count, and the set of columns that hit the distinct-value cap.
    """
    try:
        content, _ = _read_file_content(source, encoding)
    except CSVParseError as e:
        raise CSVValuesError(str(e)) from e

    if delimiter is None:
        delimiter = _detect_delimiter(content[:2000])

    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
    if reader.fieldnames is None:
        raise CSVValuesError(f"CSV file '{source}' has no header row")

    fieldnames = set(reader.fieldnames)
    missing = [c for c in columns if c not in fieldnames]
    if missing:
        raise CSVValuesError(
            f"Column(s) not found in '{source}': {', '.join(missing)}"
        )

    counters: dict[str, Counter[str]] = {name: Counter() for name in columns}
    truncated: set[str] = set()
    total_rows = 0

    try:
        for row in reader:
            total_rows += 1
            for name in columns:
                if name in truncated:
                    continue
                value = row.get(name)
                if not value:
                    continue
                counter = counters[name]
                if value not in counter and len(counter) >= max_distinct_values:
                    truncated.add(name)
                    logger.warning(
                        "Column '%s' hit the distinct-value cap (%d); "
                        "its coverage will be reported as a lower bound.",
                        name,
                        max_distinct_values,
                    )
                    continue
                counter[value] += 1
    except csv.Error as e:
        raise CSVValuesError(f"Failed to parse CSV '{source}': {e}") from e

    if total_rows == 0:
        raise CSVValuesError(f"CSV file '{source}' has no data rows")

    return CSVValues(columns=counters, total_rows=total_rows, truncated=truncated)