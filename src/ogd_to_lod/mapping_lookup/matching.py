from __future__ import annotations

import difflib
import re

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

LABEL_MATCH_THRESHOLD = 0.6     # minimum similarity for a property to count as a match for a column
DATATYPE_BONUS = 0.15           # score bonus when the datatype is compatible
TOP_N_CANDIDATES = 3            # max number of templates returned
TEMPLATE_SCORE_THRESHOLD = 0.4  # templates below this score count as "no candidate"




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


def _datatype_bonus(column_type: str, prop_datatype: str | None) -> float:
    if not prop_datatype:
        return 0.0
    equivalents = _DATATYPE_EQUIVALENTS.get(column_type, set())
    return DATATYPE_BONUS if prop_datatype in equivalents else 0.0


def _best_property_for_column(column: dict, properties: list[Property]) -> ColumnMatch | None:
    """Find the best-matching property (by label similarity + datatype bonus) for a CSV column."""
    best: ColumnMatch | None = None
    for prop in properties:
        similarity = _label_similarity(column["name"], prop.label)
        if similarity < LABEL_MATCH_THRESHOLD:
            continue

        score = similarity + _datatype_bonus(column["type"], prop.datatype)
        if best is None or score > best.score:
            best = ColumnMatch(column_name=column["name"], property=prop, score=score)

    return best


def score_template(csv_schema: dict, template: MappingTemplate) -> TemplateMatch:
    """Score a single template against the csv_schema.

    Score = coverage (share of CSV columns with a match) * average similarity
    of the matches found.
    """
    columns = csv_schema["columns"]
    matches = [
        match
        for column in columns
        if (match := _best_property_for_column(column, template.properties)) is not None
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
        top_n: int = TOP_N_CANDIDATES,
        threshold: float = TEMPLATE_SCORE_THRESHOLD,
) -> list[TemplateMatch]:
    """Score all templates, drop those below the threshold, return the top-N by score."""
    scored = [score_template(csv_schema, template) for template in templates]
    candidates = [tm for tm in scored if tm.score >= threshold]
    candidates.sort(key=lambda tm: tm.score, reverse=True)
    return candidates[:top_n]