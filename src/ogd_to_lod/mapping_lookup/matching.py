from __future__ import annotations

import difflib
import re

from ogd_to_lod.config import MappingTemplateConfig
from ogd_to_lod.mapping_lookup.models import ColumnMatch, MappingTemplate, Property, TemplateMatch

_DATATYPE_EQUIVALENTS: dict[str, set[str]] = {
    "integer": {"xsd:integer", "xsd:int", "xsd:long", "xsd:nonNegativeInteger"},
    "decimal": {"xsd:decimal", "xsd:float", "xsd:double"},
    "float": {"xsd:decimal", "xsd:float", "xsd:double"},
    "string": {"xsd:string"},
    "date": {"xsd:date"},
    "datetime": {"xsd:dateTime"},
    "boolean": {"xsd:boolean"},
}


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[_\-]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _label_similarity(column_name: str, label: str | None) -> float:
    if not label:
        return 0.0
    a, b = _normalize(column_name), _normalize(label)
    return difflib.SequenceMatcher(None, a, b).ratio()


def _datatype_compatible(column_type: str, prop_datatype: str | None) -> bool:
    """Strict compatibility check: reject unless the datatypes are known to align.
    """
    if not prop_datatype:
        return False
    equivalents = _DATATYPE_EQUIVALENTS.get(column_type, set())
    return prop_datatype in equivalents


def _best_property_for_column(column: dict, label_match_threshold: float ,properties: list[Property]) -> ColumnMatch | None:
    """Find the best-matching property (by label similarity) for a CSV column.

    Properties whose datatype is known and incompatible with the column's
    type are rejected outright, even if the label matches well.
    """
    best: ColumnMatch | None = None
    for prop in properties:
        similarity = _label_similarity(column["name"], prop.label)
        if similarity < label_match_threshold:
            continue

        if not _datatype_compatible(column["type"], prop.datatype):
            continue

        if best is None or similarity > best.score:
            best = ColumnMatch(column_name=column["name"], property=prop, score=similarity)

    return best


def score_template(csv_schema: dict, label_match_threshold: float,template: MappingTemplate) -> TemplateMatch:
    """Score a single template against the csv_schema.

    Score = coverage (share of CSV columns with a match) * average similarity
    of the matches found.
    """
    columns = csv_schema["columns"]
    matches = [
        match
        for column in columns
        if (match := _best_property_for_column(column,label_match_threshold, template.properties)) is not None
    ]

    coverage = len(matches) / len(columns) if columns else 0.0
    avg_similarity = sum(m.score for m in matches) / len(matches) if matches else 0.0

    return TemplateMatch(
        template=template,
        column_matches=matches,
        score=coverage * avg_similarity,
    )


def rank_templates(
        csv_schema: dict,
        templates: list[MappingTemplate],
        config: MappingTemplateConfig,
) -> list[TemplateMatch]:
    """Score all templates, drop those below the threshold, return the top-N by score."""
    scored = [score_template(csv_schema, config.label_match_threshold, template) for template in templates]
    candidates = [tm for tm in scored if tm.score >= config.template_score_threshold]
    candidates.sort(key=lambda tm: tm.score, reverse=True)
    return candidates[:config.top_n_candidates]