"""RML generation module for creating RDF Mapping Language configurations."""

from ogd_to_lod.rml.generator import (
    CSV_SOURCE_PLACEHOLDER,
    RMLGenerationError,
    RMLGenerator,
    generate_rml,
)
from ogd_to_lod.rml.postprocess import replace_csv_source_with_placeholder
from ogd_to_lod.rml.prompts import RML_CORRECTION_PROMPT, RML_GENERATION_PROMPT

__all__ = [
    "RMLGenerator",
    "RMLGenerationError",
    "generate_rml",
    "replace_csv_source_with_placeholder",
    "RML_CORRECTION_PROMPT",
    "RML_GENERATION_PROMPT",
    "CSV_SOURCE_PLACEHOLDER",
]
