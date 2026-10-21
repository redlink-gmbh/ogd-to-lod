"""Huwise mapping preparation of YARRRML with injected DefinedTermSets (#43)."""

import yaml

from ogd_to_lod._iris import dataset_iris
from ogd_to_lod.huwise.prepare_mapping_for_huwise import prepare_mapping
from ogd_to_lod.rml.termsets import add_defined_term_sets


def test_term_set_link_resolves_to_mapping_key(sample_rml):
    """The inDefinedTermSet object becomes the key of the injected set mapping."""
    yarrrml = add_defined_term_sets(sample_rml, dataset_iris("https://example.org/", "x"))

    out, _ = prepare_mapping(
        yarrrml,
        domain="example.org",
        dataset_id=None,
        field_map={"year": "year", "region": "region", "value": "value"},
        headers={},
    )
    mappings = yaml.safe_load(out)["mappings"]

    links = [
        row for row in mappings["regionCodes"]["predicateobjects"]
        if row[0] == "http://schema.org/inDefinedTermSet"
    ]
    assert links == [["http://schema.org/inDefinedTermSet", "regionCodesTermSet"]]
    assert mappings["regionCodesTermSet"]["subject"] == "https://example.org/x/termset/RAUM"
