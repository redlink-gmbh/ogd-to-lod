"""Tests for static RDF metadata generation (cube:Cube + ObservationSet)."""

from ogd_to_lod.metadata import MetadataGenerator, generate_metadata


BASE = "https://example.org/datasets/foo/"


def test_minimum_cube_and_observation_set():
    """Without dataset context, only the bare cube + observation-set are emitted."""
    ttl = generate_metadata(BASE, None)

    assert "@prefix cube: <https://cube.link/> ." in ttl
    assert f"<{BASE}> a cube:Cube" in ttl
    assert f"cube:observationSet <{BASE}observation-set>" in ttl
    assert f"<{BASE}observation-set> a cube:ObservationSet ." in ttl


def test_title_and_description_emitted():
    ttl = generate_metadata(
        BASE,
        {"title": "Population Statistics", "description": "Yearly counts."},
    )
    assert 'schema:name "Population Statistics"' in ttl
    assert 'schema:description "Yearly counts."' in ttl


def test_publisher_keywords_identifier():
    ttl = generate_metadata(
        BASE,
        {
            "publisher": "BFS",
            "keywords": ["population", "statistics"],
            "identifier": "bfs-pop-2024",
        },
    )
    assert 'schema:publisher "BFS"' in ttl
    assert '"population"' in ttl and '"statistics"' in ttl
    assert "schema:keywords" in ttl
    assert 'dcterms:identifier "bfs-pop-2024"' in ttl


def test_iso_date_typed_as_xsd_date():
    ttl = generate_metadata(BASE, {"issued": "2024-03-15"})
    assert 'dcterms:issued "2024-03-15"^^xsd:date' in ttl


def test_iso_datetime_typed_as_xsd_dateTime():
    ttl = generate_metadata(BASE, {"modified": "2024-03-15T10:00:00Z"})
    assert 'dcterms:modified "2024-03-15T10:00:00Z"^^xsd:dateTime' in ttl


def test_non_iso_date_falls_back_to_string():
    ttl = generate_metadata(BASE, {"issued": "March 2024"})
    assert 'dcterms:issued "March 2024"' in ttl
    assert "xsd:date" not in ttl


def test_license_iri_vs_string():
    iri_ttl = generate_metadata(BASE, {"license": "https://example.org/lic"})
    str_ttl = generate_metadata(BASE, {"license": "CC-BY-4.0"})
    assert "dcterms:license <https://example.org/lic>" in iri_ttl
    assert 'dcterms:license "CC-BY-4.0"' in str_ttl


def test_string_escaping():
    ttl = generate_metadata(BASE, {"title": 'He said "hi"\nand left'})
    assert '"He said \\"hi\\"\\nand left"' in ttl


def test_base_uri_without_trailing_slash():
    ttl = MetadataGenerator().generate("https://example.org/foo", None)
    assert "<https://example.org/foo> a cube:Cube" in ttl
    assert "<https://example.org/foo/observation-set> a cube:ObservationSet" in ttl


def test_empty_keywords_omits_property():
    ttl = generate_metadata(BASE, {"keywords": []})
    assert "schema:keywords" not in ttl


def test_only_observation_set_link_when_context_empty():
    """The cube:observationSet link is always emitted, even without metadata."""
    ttl = generate_metadata(BASE, {})
    assert "cube:observationSet" in ttl
