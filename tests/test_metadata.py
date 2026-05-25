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


def test_output_folder_scopes_cube_iri():
    """When output_folder is provided, the cube IRI is appended with the slug."""
    ttl = generate_metadata(BASE, None, output_folder="luft-basel")

    assert "<https://example.org/datasets/foo/luft-basel> a cube:Cube" in ttl
    assert (
        "cube:observationSet "
        "<https://example.org/datasets/foo/luft-basel/observation-set>"
    ) in ttl
    assert (
        "<https://example.org/datasets/foo/luft-basel/observation-set> "
        "a cube:ObservationSet ."
    ) in ttl


def test_output_folder_is_slugified():
    """Whitespace and case in the output folder are normalised into the IRI."""
    ttl = generate_metadata(BASE, None, output_folder="Luft Basel 2024")
    assert "<https://example.org/datasets/foo/luft-basel-2024> a cube:Cube" in ttl


def test_output_folder_works_without_trailing_slash_base():
    ttl = MetadataGenerator().generate(
        "https://example.org/foo", None, output_folder="bar"
    )
    assert "<https://example.org/foo/bar> a cube:Cube" in ttl
    assert "<https://example.org/foo/bar/observation-set> a cube:ObservationSet" in ttl


# ---------- Per-property metadata blocks ----------


def _proposal(dimensions, measures):
    return {"dimensions": dimensions, "measures": measures, "skipped_columns": []}


def test_property_blocks_emitted_for_dimensions_and_measures():
    ttl = generate_metadata(
        BASE,
        {
            "column_contexts": {
                "year": {"description": "The year of observation."},
                "region": {"description": "Statistical region code."},
                "value": {"description": "Observed value."},
            }
        },
        output_folder="population",
        mapping_proposal=_proposal(
            dimensions=[
                {"column": "year", "type": "temporal"},
                {"column": "region", "type": "spatial"},
            ],
            measures=[{"column": "value"}],
        ),
    )
    assert (
        "<https://example.org/datasets/foo/population/property/ZEIT> "
        "a cube:KeyDimension"
    ) in ttl
    assert 'schema:name "year"' in ttl
    assert 'schema:description "The year of observation."' in ttl
    assert (
        "<https://example.org/datasets/foo/population/property/RAUM> "
        "a cube:KeyDimension"
    ) in ttl
    assert (
        "<https://example.org/datasets/foo/population/property/value> "
        "a cube:MeasureDimension"
    ) in ttl


def test_property_block_sanitisation_matches_prompt_convention():
    ttl = generate_metadata(
        BASE,
        {"column_contexts": {}},
        output_folder="aq",
        mapping_proposal=_proposal(
            dimensions=[],
            measures=[
                {"column": "O3 [ug/m3]"},
                {"column": "PM2.5 [ug/m3]"},
            ],
        ),
    )
    assert "<https://example.org/datasets/foo/aq/property/O3_ug_m3>" in ttl
    assert "<https://example.org/datasets/foo/aq/property/PM2_5_ug_m3>" in ttl


def test_no_mapping_proposal_emits_no_property_blocks():
    ttl = generate_metadata(BASE, None, output_folder="x", mapping_proposal=None)
    assert "property/" not in ttl


def test_property_block_falls_back_to_header_when_no_description():
    """schema:name is always emitted from the column header, even without context."""
    ttl = generate_metadata(
        BASE,
        None,
        output_folder="x",
        mapping_proposal=_proposal(
            dimensions=[{"column": "Region"}],
            measures=[],
        ),
    )
    assert 'schema:name "Region"' in ttl
    # The cube block's schema:description doesn't apply here — search
    # inside the property block specifically.
    property_block = ttl.split("a cube:KeyDimension")[1]
    assert "schema:description" not in property_block


def test_property_block_comment_emitted_as_disambiguating_description():
    ttl = generate_metadata(
        BASE,
        {"column_contexts": {"region": {"description": "d", "comment": "c"}}},
        output_folder="x",
        mapping_proposal=_proposal(
            dimensions=[{"column": "region"}],
            measures=[],
        ),
    )
    assert 'schema:disambiguatingDescription "c"' in ttl


def test_colliding_property_iris_emit_one_merged_block():
    """Two temporal dimensions collide on ZEIT — emit one block, first wins."""
    ttl = generate_metadata(
        BASE,
        {
            "column_contexts": {
                "datetime": {"description": "Zeitstempel in UTC."},
                "ZEIT_LOCAL": {"description": "Local-time timestamp."},
            }
        },
        output_folder="x",
        mapping_proposal=_proposal(
            dimensions=[
                {"column": "datetime", "type": "temporal"},
                {"column": "ZEIT_LOCAL", "type": "temporal"},
            ],
            measures=[],
        ),
    )

    # Exactly one block for the ZEIT IRI, with the FIRST column's name.
    iri = "<https://example.org/datasets/foo/x/property/ZEIT>"
    assert ttl.count(f"{iri} a cube:KeyDimension") == 1
    assert 'schema:name "datetime"' in ttl
    assert 'schema:name "ZEIT_LOCAL"' not in ttl
    # First entry's description survives.
    assert 'schema:description "Zeitstempel in UTC."' in ttl


def test_collision_back_fills_missing_description_from_second_entry():
    """First column has no description; second does — second's wins."""
    ttl = generate_metadata(
        BASE,
        {
            "column_contexts": {
                # "datetime" deliberately missing — no entry in column_contexts.
                "ZEIT_LOCAL": {"description": "Local-time fallback."},
            }
        },
        output_folder="x",
        mapping_proposal=_proposal(
            dimensions=[
                {"column": "datetime", "type": "temporal"},
                {"column": "ZEIT_LOCAL", "type": "temporal"},
            ],
            measures=[],
        ),
    )
    # First column's name is kept, second's description back-fills.
    assert 'schema:name "datetime"' in ttl
    assert 'schema:description "Local-time fallback."' in ttl
