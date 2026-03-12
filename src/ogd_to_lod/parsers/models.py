"""Data models for parsed CSV and DCAT input."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ColumnType(Enum):
    """Detected column data types."""

    STRING = "string"
    INTEGER = "int"
    FLOAT = "float"
    DATE = "date"
    DATETIME = "datetime"
    BOOLEAN = "boolean"
    UNKNOWN = "unknown"


@dataclass
class ColumnInfo:
    """Information about a CSV column."""

    name: str
    detected_type: ColumnType
    sample_values: list[Any] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Ensure sample_values is a list."""
        if self.sample_values is None:
            self.sample_values = []


@dataclass
class CSVData:
    """Parsed CSV data with schema information."""

    source: str  # File path or URL
    columns: list[ColumnInfo] = field(default_factory=list)
    sample_rows: list[dict[str, Any]] = field(default_factory=list)
    total_rows: int = 0
    encoding: str = "utf-8"
    delimiter: str = ","

    def column_names(self) -> list[str]:
        """Return list of column names."""
        return [col.name for col in self.columns]

    def column_types(self) -> dict[str, ColumnType]:
        """Return mapping of column names to detected types."""
        return {col.name: col.detected_type for col in self.columns}


@dataclass
class TemporalCoverage:
    """Temporal coverage information from DCAT metadata."""

    start_date: str | None = None
    end_date: str | None = None


@dataclass
class SpatialCoverage:
    """Spatial coverage information from DCAT metadata."""

    location: str | None = None
    geometry: str | None = None
    bbox: str | None = None


@dataclass
class DCATMetadata:
    """Parsed DCAT metadata."""

    source: str  # File path or URL
    title: str | None = None
    description: str | None = None
    publisher: str | None = None
    keywords: list[str] = field(default_factory=list)
    temporal_coverage: TemporalCoverage | None = None
    spatial_coverage: SpatialCoverage | None = None
    identifier: str | None = None
    issued: str | None = None
    modified: str | None = None
    language: str | None = None
    license: str | None = None
    access_rights: str | None = None
    contact_point: str | None = None
    raw_content: str | None = None
    source_format: str | None = None

    def __post_init__(self) -> None:
        """Ensure keywords is a list."""
        if self.keywords is None:
            self.keywords = []


@dataclass
class ColumnContext:
    """User-provided context for a single CSV column."""

    header_name: str
    description: str | None = None
    comment: str | None = None


@dataclass
class DatasetContext:
    """Normalized dataset context extracted from one or more context files.

    Replaces DCATMetadata as the unified representation of dataset-level
    and column-level metadata, regardless of the original input format.
    """

    sources: list[str] = field(default_factory=list)
    title: str | None = None
    description: str | None = None
    publisher: str | None = None
    keywords: list[str] = field(default_factory=list)
    temporal_coverage: TemporalCoverage | None = None
    spatial_coverage: SpatialCoverage | None = None
    identifier: str | None = None
    issued: str | None = None
    modified: str | None = None
    language: str | None = None
    license: str | None = None
    access_rights: str | None = None
    contact_point: str | None = None
    column_contexts: dict[str, ColumnContext] = field(default_factory=dict)
    source_format: str | None = None  # "dcat" | "freetext" | "markdown" | "json" | "mixed"
    raw_content: str | None = None  # Combined raw content of all source files

    def __post_init__(self) -> None:
        """Ensure list fields are initialised."""
        if self.keywords is None:
            self.keywords = []
        if self.column_contexts is None:
            self.column_contexts = {}


@dataclass
class ParsedInput:
    """Unified data model combining CSV data and DCAT metadata."""

    csv_data: CSVData
    dcat_metadata: DCATMetadata | None = None

    def summary(self) -> str:
        """Return a human-readable summary of the parsed input."""
        lines = []
        lines.append(f"CSV Source: {self.csv_data.source}")
        lines.append(f"Columns: {len(self.csv_data.columns)}")
        lines.append(f"Total Rows: {self.csv_data.total_rows}")
        lines.append(f"Sample Rows: {len(self.csv_data.sample_rows)}")
        lines.append("")
        lines.append("Column Details:")
        for col in self.csv_data.columns:
            lines.append(f"  - {col.name}: {col.detected_type.value}")

        if self.dcat_metadata:
            lines.append("")
            lines.append("DCAT Metadata:")
            if self.dcat_metadata.title:
                lines.append(f"  Title: {self.dcat_metadata.title}")
            if self.dcat_metadata.description:
                lines.append(f"  Description: {self.dcat_metadata.description[:100]}...")
            if self.dcat_metadata.publisher:
                lines.append(f"  Publisher: {self.dcat_metadata.publisher}")
            if self.dcat_metadata.keywords:
                lines.append(f"  Keywords: {', '.join(self.dcat_metadata.keywords)}")
            if self.dcat_metadata.temporal_coverage:
                tc = self.dcat_metadata.temporal_coverage
                lines.append(f"  Temporal: {tc.start_date} - {tc.end_date}")
            if self.dcat_metadata.spatial_coverage:
                sc = self.dcat_metadata.spatial_coverage
                lines.append(f"  Spatial: {sc.location}")

        return "\n".join(lines)
