"""Parsers for CSV and DCAT metadata."""

from .context_normalizer import ContextNormalizer
from .context_parser import ContextParseError, parse_context
from .csv_parser import CSVParseError, parse_csv
from .dcat_parser import DCATParseError, dcat_format_to_extension, parse_dcat
from .models import (
    ColumnContext,
    ColumnInfo,
    ColumnType,
    CSVData,
    DatasetContext,
    DCATMetadata,
    ParsedInput,
    SpatialCoverage,
    TemporalCoverage,
)

__all__ = [
    # CSV parser
    "parse_csv",
    "CSVParseError",
    # DCAT parser
    "parse_dcat",
    "DCATParseError",
    "dcat_format_to_extension",
    # Context parser
    "parse_context",
    "ContextParseError",
    "ContextNormalizer",
    # Models
    "ColumnContext",
    "ColumnInfo",
    "ColumnType",
    "CSVData",
    "DatasetContext",
    "DCATMetadata",
    "ParsedInput",
    "SpatialCoverage",
    "TemporalCoverage",
]
