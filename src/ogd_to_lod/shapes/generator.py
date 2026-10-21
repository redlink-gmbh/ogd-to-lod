"""SHACL shapes (cube.link ``cube:Constraint``) for generated observations.

Produces ``shapes.ttl``: one ``sh:NodeShape, cube:Constraint`` targeting
``cube:Observation`` with one property shape per dimension / measure.

The generated YARRRML is the source of truth for predicates, datatypes and
IRI-vs-literal objects (it is the only place where they are fixed, incl.
reused SPARQL predicates). The mapping proposal adds the dimension/measure
role, the full-CSV column statistics add cardinality and string patterns,
and the reuse context adds ``sh:in`` lists for reused DefinedTermSets.
Deterministic — no AI calls.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml
from rdflib import RDF, XSD, BNode, Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection

from ogd_to_lod._iris import dataset_iris
from ogd_to_lod.logging import get_logger
from ogd_to_lod.lookup.csv_values import CSVValuesError, get_column_values
from ogd_to_lod.rml.yarrrml import (
    RDF_TYPE,
    MappingView,
    PredicateObject,
    iter_mappings,
    load_yarrrml,
    local_name,
)

if TYPE_CHECKING:
    from ogd_to_lod.lookup import ReuseContext

logger = get_logger(__name__)

SH = Namespace("http://www.w3.org/ns/shacl#")
CUBE = Namespace("https://cube.link/")
SCHEMA = Namespace("http://schema.org/")

# property local names the RML prompt reserves for key dimensions
_KEY_LOCAL_NAMES = {"ZEIT", "RAUM"}

# string pattern families, tried in order; first one matching all values wins
_DIGITS = re.compile(r"[0-9]+")
_CODE = re.compile(r"[A-Z0-9_-]+")


class ShapesGenerationError(Exception):
    """Raised when no shapes can be derived from the YARRRML."""


@dataclass
class ColumnStats:
    """Full-CSV statistics for one column.

    ``values`` is ``None`` when the column hit the distinct-value cap —
    value-based patterns are then skipped. ``empty_count`` is ``None`` only
    when unknown; the column then counts as nullable.
    """

    total_rows: int
    empty_count: int | None
    values: Counter[str] | None


def compute_column_stats(csv_schema: dict[str, Any]) -> dict[str, ColumnStats] | None:
    """One streaming pass over the full CSV; ``None`` if it can't be read."""
    columns = [c["name"] for c in csv_schema.get("columns", [])]
    if not columns or not csv_schema.get("source"):
        return None
    try:
        result = get_column_values(
            source=csv_schema["source"],
            columns=columns,
            encoding=csv_schema.get("encoding"),
            delimiter=csv_schema.get("delimiter"),
        )
    except CSVValuesError as e:
        logger.warning("Column statistics unavailable, shapes use minCount 0: %s", e)
        return None

    return {
        name: ColumnStats(
            total_rows=result.total_rows,
            empty_count=result.empty_counts.get(name),
            # truncated counters are incomplete → no value-based patterns
            values=None if name in result.truncated else counter,
        )
        for name, counter in result.columns.items()
    }


def generate_shapes(
    yarrrml: str,
    base_uri: str,
    output_folder: str | None = None,
    mapping_proposal: dict[str, Any] | None = None,
    column_stats: dict[str, ColumnStats] | None = None,
    dataset_context: dict[str, Any] | None = None,
    reuse_context: ReuseContext | None = None,
) -> str:
    """Build the SHACL shapes Turtle for the observations of ``yarrrml``.

    Raises:
        ShapesGenerationError: YARRRML can't be parsed or has no
            ``cube:Observation`` mapping.
    """
    try:
        doc = load_yarrrml(yarrrml)
    except (yaml.YAMLError, ValueError) as e:
        raise ShapesGenerationError(f"Cannot parse YARRRML: {e}") from e

    views = iter_mappings(doc)
    observations = [v for v in views if str(CUBE.Observation) in v.types()]
    if not observations:
        raise ShapesGenerationError("YARRRML has no cube:Observation mapping")

    iris = dataset_iris(base_uri, output_folder)
    proposal = mapping_proposal or {}
    dim_columns = {d.get("column") for d in proposal.get("dimensions", []) or []}
    measure_columns = {m.get("column") for m in proposal.get("measures", []) or []}
    column_contexts = (dataset_context or {}).get("column_contexts") or {}
    reused_terms = _reused_term_uris(reuse_context)
    term_sets = _term_sets_by_template(views)

    g = Graph()
    for prefix, ns in (("sh", SH), ("cube", CUBE), ("schema", SCHEMA), ("xsd", XSD)):
        g.bind(prefix, ns, replace=True)  # rdflib pre-binds schema: to https://schema.org/

    shape = URIRef(iris.shape)
    g.add((shape, RDF.type, SH.NodeShape))
    g.add((shape, RDF.type, CUBE.Constraint))
    g.add((shape, SH.targetClass, CUBE.Observation))
    g.add((shape, SH.closed, Literal(True)))

    # rdf:type must be listed explicitly because the shape is closed
    classes = sorted({t for v in observations for t in v.types()})
    type_shape = _add_property_shape(g, shape, URIRef(RDF_TYPE))
    g.add((type_shape, SH.nodeKind, SH.IRI))
    g.add((type_shape, SH["in"], _rdf_list(g, [URIRef(c) for c in classes])))
    g.add((type_shape, SH.minCount, Literal(1)))

    seen: dict[str, PredicateObject] = {}
    for view in observations:
        for po in view.po:
            if po.predicate == RDF_TYPE:
                continue
            if po.predicate in seen:
                if seen[po.predicate].obj.template != po.obj.template:
                    logger.warning(
                        "Predicate <%s> mapped twice (%s / %s); shape uses the first",
                        po.predicate, seen[po.predicate].obj.template, po.obj.template,
                    )
                continue
            seen[po.predicate] = po

            is_key = _is_key_dimension(po, dim_columns, measure_columns)
            ps = _add_property_shape(g, shape, URIRef(po.predicate))
            g.add((ps, RDF.type, CUBE.KeyDimension if is_key else CUBE.MeasureDimension))
            _add_labels(g, ps, po, column_contexts)
            _add_cardinality(g, ps, po, is_key, column_stats)
            if po.obj.is_iri:
                _add_iri_constraints(g, ps, po, reused_terms, term_sets)
            else:
                _add_literal_constraints(g, ps, po, column_stats)

    logger.info("Generated shapes.ttl with %d property shape(s)", len(seen) + 1)
    return g.serialize(format="turtle")


def validate_observations(rdf_turtle: str, shapes_turtle: str) -> tuple[bool, str]:
    """Validate RMLMapper output against the shapes with pySHACL."""
    from pyshacl import validate  # heavy import, only needed here

    data = Graph().parse(data=rdf_turtle, format="turtle")
    shapes = Graph().parse(data=shapes_turtle, format="turtle")
    conforms, _, report = validate(data, shacl_graph=shapes, inference="none")
    return bool(conforms), str(report)


def _add_property_shape(g: Graph, shape: URIRef, path: URIRef) -> BNode:
    ps = BNode()
    g.add((shape, SH.property, ps))
    g.add((ps, SH.path, path))
    return ps


def _is_key_dimension(po: PredicateObject, dims: set, measures: set) -> bool:
    columns = set(po.obj.columns)
    if columns & measures:
        return False
    if columns & dims:
        return True
    # not in the proposal: reserved names, code IRIs and constants are keys
    return local_name(po.predicate) in _KEY_LOCAL_NAMES or po.obj.is_iri or po.obj.is_constant


def _add_labels(g: Graph, ps: BNode, po: PredicateObject, column_contexts: dict) -> None:
    columns = po.obj.columns
    column = columns[0] if len(columns) == 1 else None
    g.add((ps, SCHEMA.name, Literal(column or local_name(po.predicate))))
    description = ((column_contexts.get(column) or {}).get("description") or "").strip()
    if description:
        g.add((ps, SCHEMA.description, Literal(description)))


def _add_cardinality(
    g: Graph, ps: BNode, po: PredicateObject, is_key: bool, stats: dict[str, ColumnStats] | None
) -> None:
    # keys are always required (identity); measures only if no column is ever empty
    required = is_key or po.obj.is_constant or _never_empty(po.obj.columns, stats)
    g.add((ps, SH.minCount, Literal(1 if required else 0)))
    g.add((ps, SH.maxCount, Literal(1)))


def _never_empty(columns: list[str], stats: dict[str, ColumnStats] | None) -> bool:
    if not stats:
        return False
    return all(c in stats and stats[c].empty_count == 0 for c in columns)


def _add_iri_constraints(
    g: Graph,
    ps: BNode,
    po: PredicateObject,
    reused_terms: dict[str, list[str]],
    term_sets: dict[str, list[str]],
) -> None:
    g.add((ps, SH.nodeKind, SH.IRI))
    obj = po.obj
    if obj.is_constant:
        g.add((ps, SH.hasValue, URIRef(obj.template)))
        return

    prefix = obj.literal_prefix
    if prefix.startswith(("http://", "https://")):
        g.add((ps, SH.pattern, Literal("^" + re.escape(prefix))))

    # reused codes: value must be one of the existing terms
    if len(obj.columns) == 1 and reused_terms.get(obj.columns[0]):
        terms = [URIRef(t) for t in reused_terms[obj.columns[0]]]
        g.add((ps, SH["in"], _rdf_list(g, terms)))
        return

    # newly minted codes: value must be a member of its DefinedTermSet
    for set_iri in term_sets.get(obj.template, []):
        node, member = BNode(), BNode()
        g.add((ps, SH.node, node))
        g.add((node, SH.property, member))
        g.add((member, SH.path, SCHEMA.inDefinedTermSet))
        g.add((member, SH.hasValue, URIRef(set_iri)))


def _add_literal_constraints(
    g: Graph, ps: BNode, po: PredicateObject, stats: dict[str, ColumnStats] | None
) -> None:
    obj = po.obj
    if obj.language:
        g.add((ps, SH.datatype, RDF.langString))
        return
    # RDF 1.1: a literal without datatype is xsd:string
    datatype = URIRef(obj.datatype) if obj.datatype else XSD.string
    g.add((ps, SH.datatype, datatype))

    # patterns only for plain string values taken 1:1 from a single column
    single_column = len(obj.columns) == 1 and obj.template == f"$({obj.columns[0]})"
    if datatype != XSD.string or not single_column:
        return
    pattern = _string_pattern(stats.get(obj.columns[0]) if stats else None)
    if pattern:
        g.add((ps, SH.pattern, Literal(pattern)))


def _string_pattern(stats: ColumnStats | None) -> str | None:
    """Pattern only if every value of the full CSV fits one fixed family."""
    if stats is None or not stats.values:
        return None
    values = list(stats.values)
    if all(_DIGITS.fullmatch(v) for v in values):
        lengths = {len(v) for v in values}
        return f"^[0-9]{{{lengths.pop()}}}$" if len(lengths) == 1 else "^[0-9]+$"
    if all(_CODE.fullmatch(v) for v in values):
        return "^[A-Z0-9_-]+$"
    return None


def _reused_term_uris(reuse_context: ReuseContext | None) -> dict[str, list[str]]:
    if not reuse_context:
        return {}
    return {
        c.column: c.term_uris
        for c in reuse_context.columns
        if c.template_verified and c.term_uris
    }


def _term_sets_by_template(views: list[MappingView]) -> dict[str, list[str]]:
    """DefinedTerm subject template -> DefinedTermSet IRIs it links to."""
    result: dict[str, list[str]] = {}
    for view in views:
        if view.subject is None or str(SCHEMA.DefinedTerm) not in view.types():
            continue
        sets = [
            p.obj.template
            for p in view.po
            if p.predicate == str(SCHEMA.inDefinedTermSet) and p.obj.is_iri
        ]
        if sets:
            result.setdefault(view.subject.template, []).extend(sets)
    return result


def _rdf_list(g: Graph, items: list) -> BNode:
    head = BNode()
    Collection(g, head, items)
    return head
