"""Tests for SHACL shapes generation (#43)."""

import pytest
from rdflib import RDF, XSD, Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection

from ogd_to_lod._iris import dataset_iris
from ogd_to_lod.lookup import ColumnReuse, ReuseContext
from ogd_to_lod.lookup.csv_values import get_column_values
from ogd_to_lod.rml.termsets import add_defined_term_sets
from ogd_to_lod.shapes import (
    ColumnStats,
    ShapesGenerationError,
    compute_column_stats,
    generate_shapes,
)

SH = Namespace("http://www.w3.org/ns/shacl#")
CUBE = Namespace("https://cube.link/")
SCHEMA = Namespace("http://schema.org/")

BASE = "https://example.org/"
PROP = Namespace("https://example.org/property/")
SHAPE = URIRef("https://example.org/shape")
TERM_SET = URIRef("https://example.org/termset/RAUM")

PROPOSAL = {
    "dimensions": [
        {"column": "year", "type": "temporal"},
        {"column": "region", "type": "spatial"},
    ],
    "measures": [{"column": "value"}],
}


def _stats(empty: dict[str, int] | None = None) -> dict[str, ColumnStats]:
    empty = empty or {}
    return {c: ColumnStats(7, empty.get(c, 0), None) for c in ("year", "region", "value")}


def _shapes(yarrrml: str, **kwargs) -> Graph:
    kwargs.setdefault("mapping_proposal", PROPOSAL)
    ttl = generate_shapes(yarrrml, BASE, **kwargs)
    return Graph().parse(data=ttl, format="turtle")


def _property_shape(g: Graph, path: URIRef):
    shapes = [ps for ps in g.objects(SHAPE, SH.property) if (ps, SH.path, path) in g]
    assert len(shapes) == 1, f"expected one property shape for {path}"
    return shapes[0]


def _value(g: Graph, ps, predicate):
    return g.value(ps, predicate)


# ── shape structure ──────────────────────────────────────────────────────


def test_node_shape_follows_cube_link_constraint(sample_rml):
    g = _shapes(sample_rml)

    assert (SHAPE, RDF.type, SH.NodeShape) in g
    assert (SHAPE, RDF.type, CUBE.Constraint) in g
    assert (SHAPE, SH.targetClass, CUBE.Observation) in g
    assert _value(g, SHAPE, SH.closed) == Literal(True)

    type_shape = _property_shape(g, RDF.type)
    assert list(Collection(g, _value(g, type_shape, SH["in"]))) == [CUBE.Observation]


def test_key_dimensions_and_measure(sample_rml):
    g = _shapes(sample_rml, column_stats=_stats())

    zeit = _property_shape(g, PROP.ZEIT)
    assert (zeit, RDF.type, CUBE.KeyDimension) in g
    assert _value(g, zeit, SH.datatype) == XSD.gYear
    assert _value(g, zeit, SH.minCount).toPython() == 1
    assert _value(g, zeit, SH.maxCount).toPython() == 1
    assert _value(g, zeit, SCHEMA.name) == Literal("year")

    raum = _property_shape(g, PROP.RAUM)
    assert (raum, RDF.type, CUBE.KeyDimension) in g
    assert _value(g, raum, SH.nodeKind) == SH.IRI
    assert _value(g, raum, SH.pattern) == Literal(r"^https://example\.org/code/")

    value = _property_shape(g, PROP.value)
    assert (value, RDF.type, CUBE.MeasureDimension) in g
    assert _value(g, value, SH.datatype) == XSD.decimal
    assert _value(g, value, SH.minCount).toPython() == 1


def test_nullable_measure_gets_min_count_zero(sample_rml):
    g = _shapes(sample_rml, column_stats=_stats({"value": 2}))
    assert _value(g, _property_shape(g, PROP.value), SH.minCount).toPython() == 0


def test_without_stats_measures_are_optional_keys_required(sample_rml):
    g = _shapes(sample_rml)
    assert _value(g, _property_shape(g, PROP.value), SH.minCount).toPython() == 0
    assert _value(g, _property_shape(g, PROP.ZEIT), SH.minCount).toPython() == 1


def test_shape_iri_is_dataset_scoped(sample_rml):
    ttl = generate_shapes(sample_rml, BASE, output_folder="Luft Basel")
    assert f"<{dataset_iris(BASE, 'Luft Basel').shape}>" in ttl
    assert "<https://example.org/luft-basel/shape>" in ttl


def test_description_from_dataset_context(sample_rml):
    ctx = {"column_contexts": {"value": {"description": "Messwert"}}}
    g = _shapes(sample_rml, dataset_context=ctx)
    assert _value(g, _property_shape(g, PROP.value), SCHEMA.description) == Literal("Messwert")


# ── YARRRML variants ─────────────────────────────────────────────────────


def test_reused_predicate_uri_is_used_as_path(sample_rml):
    reused = "https://ld.admin.ch/cube/dimension/Jahr"
    g = _shapes(sample_rml.replace("[ex-property:ZEIT,", f'["{reused}",'))
    assert (_property_shape(g, URIRef(reused)), RDF.type, CUBE.KeyDimension) in g


def test_dict_form_po(sample_rml):
    yarrrml = sample_rml.replace(
        "      - [ex-property:value, $(value), xsd:decimal]",
        "      - p: ex-property:value\n"
        "        o:\n"
        "          value: $(value)\n"
        "          datatype: xsd:decimal",
    )
    g = _shapes(yarrrml)
    assert _value(g, _property_shape(g, PROP.value), SH.datatype) == XSD.decimal


def test_predicate_collision_keeps_first(sample_rml):
    yarrrml = sample_rml.replace(
        "      - [ex-property:value, $(value), xsd:decimal]",
        "      - [ex-property:value, $(value), xsd:decimal]\n"
        "      - [ex-property:value, $(other), xsd:integer]",
    )
    g = _shapes(yarrrml)
    assert _value(g, _property_shape(g, PROP.value), SH.datatype) == XSD.decimal


def test_constant_iri_object_gets_has_value(sample_rml):
    g = _shapes(sample_rml.replace("ex-code:$(region)~iri", "ex-code:CH~iri"))
    raum = _property_shape(g, PROP.RAUM)
    assert _value(g, raum, SH.hasValue) == URIRef("https://example.org/code/CH")
    assert _value(g, raum, SH.minCount).toPython() == 1


def test_plain_literal_is_xsd_string_with_code_pattern(sample_rml):
    yarrrml = sample_rml.replace(
        "      - [ex-property:value, $(value), xsd:decimal]",
        "      - [ex-property:value, $(value), xsd:decimal]\n"
        "      - [ex-property:kanton, $(kanton)]",
    )
    stats = _stats() | {"kanton": ColumnStats(7, 0, {"BS": 3, "ZH": 4})}
    g = _shapes(yarrrml, column_stats=stats)
    kanton = _property_shape(g, PROP.kanton)
    assert _value(g, kanton, SH.datatype) == XSD.string
    assert _value(g, kanton, SH.pattern) == Literal("^[A-Z0-9_-]+$")


def test_fixed_length_digit_pattern(sample_rml):
    yarrrml = sample_rml.replace(
        "      - [ex-property:value, $(value), xsd:decimal]",
        "      - [ex-property:value, $(value), xsd:decimal]\n"
        "      - [ex-property:bfs, $(bfs), xsd:string]",
    )
    stats = _stats() | {"bfs": ColumnStats(7, 0, {"2701": 1, "0261": 6})}
    g = _shapes(yarrrml, column_stats=stats)
    assert _value(g, _property_shape(g, PROP.bfs), SH.pattern) == Literal("^[0-9]{4}$")


def test_no_pattern_for_mixed_values(sample_rml):
    yarrrml = sample_rml.replace(
        "      - [ex-property:value, $(value), xsd:decimal]",
        "      - [ex-property:value, $(value), xsd:decimal]\n"
        "      - [ex-property:name, $(name)]",
    )
    stats = _stats() | {"name": ColumnStats(7, 0, {"Basel": 1, "ZH": 6})}
    g = _shapes(yarrrml, column_stats=stats)
    assert _value(g, _property_shape(g, PROP.name), SH.pattern) is None


# ── code lists ───────────────────────────────────────────────────────────


def test_new_codes_must_be_in_their_defined_term_set(sample_rml):
    yarrrml = add_defined_term_sets(sample_rml, dataset_iris(BASE))
    g = _shapes(yarrrml)

    node = _value(g, _property_shape(g, PROP.RAUM), SH.node)
    member = _value(g, node, SH.property)
    assert _value(g, member, SH.path) == SCHEMA.inDefinedTermSet
    assert _value(g, member, SH.hasValue) == TERM_SET


def test_reused_codes_get_sh_in_with_all_terms(sample_rml):
    terms = ["https://ld.admin.ch/region/1", "https://ld.admin.ch/region/2"]
    reuse = ReuseContext(columns=[ColumnReuse(
        column="region", term_set_uri="https://ld.admin.ch/regions", coverage=1.0,
        distinct_coverage=1.0, uri_template="https://ld.admin.ch/region/$(region)~iri",
        template_verified=True, term_uris=terms,
    )])
    yarrrml = sample_rml.replace("ex-code:$(region)~iri", "https://ld.admin.ch/region/$(region)~iri")
    g = _shapes(yarrrml, reuse_context=reuse)

    raum = _property_shape(g, PROP.RAUM)
    assert [str(t) for t in Collection(g, _value(g, raum, SH["in"]))] == terms
    assert _value(g, raum, SH.node) is None


def test_unverified_reuse_gets_no_sh_in(sample_rml):
    reuse = ReuseContext(columns=[ColumnReuse(
        column="region", term_set_uri="https://ld.admin.ch/regions", coverage=1.0,
        distinct_coverage=1.0, uri_template=None, template_verified=False,
        term_uris=["https://ld.admin.ch/region/1"],
    )])
    g = _shapes(sample_rml, reuse_context=reuse)
    assert _value(g, _property_shape(g, PROP.RAUM), SH["in"]) is None


# ── errors ───────────────────────────────────────────────────────────────


def test_no_observation_mapping_raises():
    with pytest.raises(ShapesGenerationError):
        generate_shapes("mappings:\n  x:\n    s: ex:a\n    po:\n      - [a, ex:B]\n", BASE)


def test_unparseable_yarrrml_raises():
    with pytest.raises(ShapesGenerationError):
        generate_shapes("mappings: [unclosed", BASE)


# ── column statistics ────────────────────────────────────────────────────


def test_compute_column_stats(tmp_path):
    csv = tmp_path / "d.csv"
    csv.write_text("year,value\n2020,1.5\n2021,\n2022,3\n", encoding="utf-8")
    stats = compute_column_stats(
        {"source": str(csv), "columns": [{"name": "year"}, {"name": "value"}]}
    )
    assert stats["year"].empty_count == 0
    assert stats["value"].empty_count == 1
    assert stats["value"].values == {"1.5": 1, "3": 1}


def test_compute_column_stats_unreadable_returns_none(tmp_path):
    schema = {"source": str(tmp_path / "missing.csv"), "columns": [{"name": "a"}]}
    assert compute_column_stats(schema) is None


def test_empty_counts_exact_for_truncated_columns(tmp_path):
    csv = tmp_path / "d.csv"
    csv.write_text("a,b\nx,1\ny,2\n,3\nz,4\n,5\n", encoding="utf-8")
    result = get_column_values(str(csv), ["a"], max_distinct_values=1)
    assert "a" in result.truncated
    assert result.empty_counts == {"a": 2}


# ── pySHACL validation ───────────────────────────────────────────────────

OBS = """
@prefix cube: <https://cube.link/> .
@prefix schema: <http://schema.org/> .
@prefix p: <https://example.org/property/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
<https://example.org/observation/2020_North> a cube:Observation ;
    p:ZEIT "2020"^^xsd:gYear ;
    p:RAUM <https://example.org/code/North> ;
    p:value "100.5"^^xsd:decimal .
<https://example.org/code/North> a schema:DefinedTerm ;
    schema:name "North" ;
    schema:inDefinedTermSet <https://example.org/termset/RAUM> .
"""


@pytest.fixture
def shapes_ttl(sample_rml):
    yarrrml = add_defined_term_sets(sample_rml, dataset_iris(BASE))
    return generate_shapes(yarrrml, BASE, mapping_proposal=PROPOSAL, column_stats=_stats())


@pytest.fixture
def validate():
    pytest.importorskip("pyshacl")
    from ogd_to_lod.shapes import validate_observations

    return validate_observations


def test_conforming_observation(validate, shapes_ttl):
    conforms, report = validate(OBS, shapes_ttl)
    assert conforms, report


@pytest.mark.parametrize(
    "broken",
    [
        OBS.replace('"100.5"^^xsd:decimal', '"n/a"'),  # wrong datatype
        OBS.replace('p:ZEIT "2020"^^xsd:gYear ;', ""),  # missing key dimension
        OBS.replace("p:value", 'p:extra "x" ; p:value'),  # closed shape
        OBS.replace("schema:inDefinedTermSet <https://example.org/termset/RAUM>",
                    "schema:inDefinedTermSet <https://example.org/termset/OTHER>"),  # not in set
        OBS.replace('"100.5"^^xsd:decimal', '"1"^^xsd:decimal, "2"^^xsd:decimal'),  # maxCount
    ],
    ids=["datatype", "missing-key", "closed", "term-set", "max-count"],
)
def test_violations(validate, shapes_ttl, broken):
    conforms, report = validate(broken, shapes_ttl)
    assert not conforms
    assert "Constraint Violation" in report


def test_plain_literal_conforms_to_xsd_string(validate, sample_rml):
    yarrrml = sample_rml.replace(
        "      - [ex-property:value, $(value), xsd:decimal]",
        "      - [ex-property:value, $(value), xsd:decimal]\n"
        "      - [ex-property:kanton, $(kanton)]",
    )
    shapes = generate_shapes(yarrrml, BASE, mapping_proposal=PROPOSAL)
    data = OBS.replace("p:value", 'p:kanton "BS" ; p:value')
    conforms, report = validate(data, shapes)
    assert conforms, report


def test_reused_term_outside_sh_in_violates(validate, sample_rml):
    reuse = ReuseContext(columns=[ColumnReuse(
        column="region", term_set_uri="https://ex.org/regions", coverage=1.0,
        distinct_coverage=1.0, uri_template="https://example.org/code/$(region)~iri",
        template_verified=True, term_uris=["https://example.org/code/South"],
    )])
    shapes = generate_shapes(sample_rml, BASE, mapping_proposal=PROPOSAL, reuse_context=reuse)
    conforms, _ = validate(OBS, shapes)
    assert not conforms


# ── real pipeline: YARRRML → yarrrml-parser → RMLMapper → pySHACL ────────


@pytest.mark.integration
class TestShapesIntegration:
    """Requires Docker (rmlio/yarrrml-parser, rmlio/rmlmapper-java) and pyshacl."""

    @staticmethod
    def _run(csv_path: str, sample_rml: str):
        pytest.importorskip("pyshacl")
        from ogd_to_lod.shapes import validate_observations
        from ogd_to_lod.validation import RMLValidator

        # post-processed (re-dumped) YARRRML must still run through the pipeline
        yarrrml = add_defined_term_sets(sample_rml, dataset_iris(BASE))
        schema = {
            "source": csv_path,
            "columns": [{"name": "year"}, {"name": "region"}, {"name": "value"}],
        }
        shapes = generate_shapes(
            yarrrml, BASE, mapping_proposal=PROPOSAL,
            column_stats=compute_column_stats(schema),
        )
        result = RMLValidator(use_docker=True).validate_with_rmlmapper(yarrrml, csv_path)
        assert result.valid, result.error_message
        return result.rdf_output, shapes, validate_observations

    def test_generated_observations_conform(self, docker_available, data_csv, sample_rml):
        rdf, shapes, validate = self._run(data_csv, sample_rml)
        assert "https://example.org/termset/RAUM" in rdf
        conforms, report = validate(rdf, shapes)
        assert conforms, report

    def test_empty_measure_cell_emits_no_triple(self, docker_available, tmp_path, sample_rml):
        """Verifies the minCount-0 assumption: RMLMapper skips empty cells."""
        csv = tmp_path / "data.csv"
        csv.write_text("year,region,value\n2020,North,\n2021,South,2.5\n", encoding="utf-8")
        rdf, shapes, validate = self._run(str(csv), sample_rml)

        g = Graph().parse(data=rdf, format="turtle")
        obs = URIRef("https://example.org/observation/2020_North")
        assert g.value(obs, PROP.value) is None
        conforms, report = validate(rdf, shapes)
        assert conforms, report
