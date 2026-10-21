"""Deterministic DefinedTermSet post-processing for generated YARRRML.

For every newly minted code (a ``schema:DefinedTerm`` mapping whose subject
is used as an IRI object of a ``cube:Observation`` mapping) this adds

* ``[schema:inDefinedTermSet, "<set>~iri"]`` to the DefinedTerm mapping, and
* one mapping declaring ``<set> a schema:DefinedTermSet ; schema:name …``.

The set IRI is dataset-scoped (``<dataset>/termset/<property local name>``,
see ``ogd_to_lod._iris``). The declaration lives in the YARRRML (not in
``metadata.ttl``) because Huwise only receives the mapping, and its link
resolution needs a mapping whose subject is the set.
"""

from __future__ import annotations

import copy
from collections.abc import Collection

import yaml

from ogd_to_lod._iris import DatasetIRIs
from ogd_to_lod.logging import get_logger
from ogd_to_lod.rml.yarrrml import (
    RDF_TYPE,
    MappingView,
    dump_yarrrml,
    get_prefixes,
    iter_mappings,
    load_yarrrml,
    local_name,
)

logger = get_logger(__name__)

SCHEMA = "http://schema.org/"
CUBE_OBSERVATION = "https://cube.link/Observation"


def add_defined_term_sets(
    yarrrml: str,
    iris: DatasetIRIs,
    skip_columns: Collection[str] = (),
) -> str:
    """Link newly minted DefinedTerms to a per-dimension DefinedTermSet.

    Args:
        yarrrml: Generated YARRRML text.
        iris: Dataset IRIs (provides the term-set IRI scheme).
        skip_columns: Columns whose codes are reused from an existing term
            set (verified SPARQL templates) — those terms already exist and
            must not be re-declared.

    Returns:
        The YARRRML with term sets added. Returned unchanged (including
        comments) when nothing had to be added or the YAML can't be parsed —
        the syntax check reports broken YAML downstream.
    """
    try:
        doc = load_yarrrml(yarrrml)
    except (yaml.YAMLError, ValueError) as e:
        logger.warning("Skipping DefinedTermSet post-processing: %s", e)
        return yarrrml

    prefixes = get_prefixes(doc)
    schema_prefix = next((p for p, ns in prefixes.items() if ns == SCHEMA), None)

    def curie(local: str) -> str:
        return f"{schema_prefix}:{local}" if schema_prefix else SCHEMA + local

    views = iter_mappings(doc)

    # IRI objects of observations: template -> predicate (e.g. ex-code:$(x) -> …/RAUM)
    dimension_of: dict[str, str] = {}
    for view in views:
        if CUBE_OBSERVATION not in view.types():
            continue
        for po in view.po:
            if po.obj.is_iri and po.predicate != RDF_TYPE:
                dimension_of.setdefault(po.obj.template, po.predicate)

    declared_sets = {
        v.subject.template for v in views if v.subject and SCHEMA + "DefinedTermSet" in v.types()
    }
    mappings = doc["mappings"]
    new_mappings: dict[str, dict] = {}
    changed = False

    for view in views:
        if SCHEMA + "DefinedTerm" not in view.types() or view.subject is None:
            continue
        if set(view.subject.columns) & set(skip_columns):
            continue
        predicate = dimension_of.get(view.subject.template)
        if predicate is None:
            logger.debug("DefinedTerm mapping '%s' is not used by any observation", view.name)
            continue

        set_iri = iris.term_set(local_name(predicate))
        changed |= _link_terms(view, set_iri, curie)

        if set_iri not in declared_sets:
            columns = view.subject.columns
            label = columns[0] if len(columns) == 1 else local_name(predicate)
            new_mappings[_unique_name(f"{view.name}TermSet", mappings, new_mappings)] = (
                _term_set_mapping(view, set_iri, label, curie)
            )
            declared_sets.add(set_iri)
            changed = True

    if not changed:
        return yarrrml
    mappings.update(new_mappings)
    logger.info("Added DefinedTermSet links/declarations (%d new set(s))", len(new_mappings))
    return dump_yarrrml(doc)


def _link_terms(view: MappingView, set_iri: str, curie) -> bool:
    """Ensure the DefinedTerm mapping points to ``set_iri``; drop isPartOf."""
    entries: list = view.raw[view.po_key]
    changed = False

    # schema:isPartOf on terms was LLM improvisation — membership is inDefinedTermSet
    part_of = {id(p.raw) for p in view.po if p.predicate == SCHEMA + "isPartOf"}
    if part_of:
        logger.warning("Replacing schema:isPartOf on '%s' with schema:inDefinedTermSet", view.name)
        entries[:] = [e for e in entries if id(e) not in part_of]
        changed = True

    linked = any(
        p.predicate == SCHEMA + "inDefinedTermSet" and p.obj.template == set_iri for p in view.po
    )
    if not linked:
        entries.append([curie("inDefinedTermSet"), f"{set_iri}~iri"])
        changed = True
    return changed


def _term_set_mapping(view: MappingView, set_iri: str, label: str, curie) -> dict:
    mapping: dict = {}
    # same source as the terms, so RMLMapper emits the set alongside them
    for key in ("sources", "source"):
        if key in view.raw:
            mapping[key] = copy.deepcopy(view.raw[key])  # no YAML anchors on dump
    # plain constant IRI (not the {value, type: iri} long form): Huwise's
    # prepare_mapping only resolves links against string subjects
    mapping["s"] = set_iri
    mapping["po"] = [["a", curie("DefinedTermSet")], [curie("name"), label]]
    return mapping


def _unique_name(name: str, *taken: dict) -> str:
    candidate, i = name, 2
    while any(candidate in t for t in taken):
        candidate, i = f"{name}{i}", i + 1
    return candidate
