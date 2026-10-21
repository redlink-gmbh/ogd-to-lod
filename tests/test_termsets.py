"""Tests for the DefinedTermSet YARRRML post-processing (#43)."""

import yaml

from ogd_to_lod._iris import dataset_iris
from ogd_to_lod.rml.termsets import add_defined_term_sets

BASE = "https://example.org/"
IRIS = dataset_iris(BASE, "luft-basel")
SET_IRI = "https://example.org/luft-basel/termset/RAUM"


def _mappings(text: str) -> dict:
    return yaml.safe_load(text)["mappings"]


def test_terms_linked_and_set_declared(sample_rml):
    out = _mappings(add_defined_term_sets(sample_rml, IRIS))

    assert ["schema:inDefinedTermSet", f"{SET_IRI}~iri"] in out["regionCodes"]["po"]
    term_set = out["regionCodesTermSet"]
    assert term_set["s"] == SET_IRI
    assert ["a", "schema:DefinedTermSet"] in term_set["po"]
    assert ["schema:name", "region"] in term_set["po"]
    # same source as the terms
    assert term_set["sources"] == out["regionCodes"]["sources"]


def test_idempotent(sample_rml):
    once = add_defined_term_sets(sample_rml, IRIS)
    assert add_defined_term_sets(once, IRIS) == once


def test_output_has_no_yaml_aliases(sample_rml):
    out = add_defined_term_sets(sample_rml, IRIS)
    assert "&id" not in out and "*id" not in out


def test_text_unchanged_without_defined_terms():
    yarrrml = (
        "# comment kept\n"
        "prefixes:\n  cube: https://cube.link/\n  ex-property: https://example.org/property/\n"
        "mappings:\n  observations:\n    s: https://example.org/obs/$(id)\n    po:\n"
        "      - [a, cube:Observation]\n      - [ex-property:value, $(value)]\n"
    )
    assert add_defined_term_sets(yarrrml, IRIS) == yarrrml


def test_reused_columns_are_skipped(sample_rml):
    assert add_defined_term_sets(sample_rml, IRIS, skip_columns={"region"}) == sample_rml


def test_is_part_of_replaced(sample_rml):
    yarrrml = sample_rml.replace(
        "      - [schema:name, $(region)]",
        "      - [schema:name, $(region)]\n      - [schema:isPartOf, ex:region-set~iri]",
    )
    po = _mappings(add_defined_term_sets(yarrrml, IRIS))["regionCodes"]["po"]

    assert all(entry[0] != "schema:isPartOf" for entry in po)
    assert ["schema:inDefinedTermSet", f"{SET_IRI}~iri"] in po


def test_full_iris_when_schema_prefix_missing(sample_rml):
    yarrrml = sample_rml.replace('  schema: "http://schema.org/"\n', "").replace(
        "schema:DefinedTerm", "http://schema.org/DefinedTerm"
    )
    out = _mappings(add_defined_term_sets(yarrrml, IRIS))

    assert ["http://schema.org/inDefinedTermSet", f"{SET_IRI}~iri"] in out["regionCodes"]["po"]
    assert ["a", "http://schema.org/DefinedTermSet"] in out["regionCodesTermSet"]["po"]


def test_unparseable_yaml_returned_unchanged():
    broken = "mappings: [unclosed"
    assert add_defined_term_sets(broken, IRIS) == broken
