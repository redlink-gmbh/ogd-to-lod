"""Tests for SPARQL-based vocabulary reuse lookup."""

from unittest.mock import MagicMock, patch

import pytest

from ogd_to_lod.lookup import ReuseContext, SPARQLLookup
from ogd_to_lod.lookup.reuse_context import MatchedDefinedTermSet, MatchedProperty
from ogd_to_lod.lookup.sparql_client import MIN_COVERAGE


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_CSV_SCHEMA = {
    "columns": [
        {"name": "JAHR", "type": "integer", "samples": ["2020", "2021", "2022"]},
        {"name": "QUARTIER", "type": "string", "samples": ["Kreis 1", "Kreis 2", "Kreis 3"]},
        {"name": "ANZAHL", "type": "decimal", "samples": ["100", "200", "300"]},
    ]
}

SAMPLE_MAPPING_PROPOSAL = {
    "dimensions": [
        {"column": "JAHR", "type": "temporal"},
        {"column": "QUARTIER", "type": "categorical"},
    ],
    "measures": [
        {"column": "ANZAHL", "unit": None},
    ],
}


# ---------------------------------------------------------------------------
# ReuseContext
# ---------------------------------------------------------------------------

class TestReuseContext:
    def test_empty_has_no_matches(self):
        ctx = ReuseContext()
        assert not ctx.has_matches()

    def test_with_property_has_matches(self):
        ctx = ReuseContext(
            properties=[
                MatchedProperty(
                    existing_uri="https://example.org/property/ZEIT",
                    label="Zeit",
                    matched_column="JAHR",
                )
            ]
        )
        assert ctx.has_matches()

    def test_with_defined_term_set_has_matches(self):
        ctx = ReuseContext(
            defined_term_sets=[
                MatchedDefinedTermSet(
                    term_set_uri="https://example.org/code/quartier/",
                    uri_template="https://example.org/code/$(QUARTIER)~iri",
                    matched_column="QUARTIER",
                    coverage=1.0,
                )
            ]
        )
        assert ctx.has_matches()

    def test_to_prompt_text_empty(self):
        assert ReuseContext().to_prompt_text() == ""

    def test_to_prompt_text_with_property(self):
        ctx = ReuseContext(
            properties=[
                MatchedProperty(
                    existing_uri="https://example.org/property/ZEIT",
                    label="Zeit",
                    matched_column="JAHR",
                )
            ]
        )
        text = ctx.to_prompt_text()
        assert "https://example.org/property/ZEIT" in text
        assert "JAHR" in text
        assert "Properties" in text

    def test_to_prompt_text_with_defined_term_set(self):
        ctx = ReuseContext(
            defined_term_sets=[
                MatchedDefinedTermSet(
                    term_set_uri="https://example.org/code/quartier/",
                    uri_template="https://example.org/code/$(QUARTIER)~iri",
                    matched_column="QUARTIER",
                    coverage=0.75,
                )
            ]
        )
        text = ctx.to_prompt_text()
        assert "https://example.org/code/$(QUARTIER)~iri" in text
        assert "QUARTIER" in text
        assert "DefinedTermSets" in text
        assert "75%" in text

    def test_to_prompt_text_instructs_no_separate_mapping(self):
        ctx = ReuseContext(
            defined_term_sets=[
                MatchedDefinedTermSet(
                    term_set_uri="https://example.org/ts",
                    uri_template="https://example.org/code/$(COL)~iri",
                    matched_column="COL",
                    coverage=1.0,
                )
            ]
        )
        assert "Do NOT generate a separate mapping" in ctx.to_prompt_text()

    def test_to_display_text_empty(self):
        assert ReuseContext().to_display_text() == ""


# ---------------------------------------------------------------------------
# SPARQLLookup — property lookup
# ---------------------------------------------------------------------------

PROPERTY_ROWS = [
    {"property": "https://example.org/property/ZEIT", "label": "JAHR"},
    {"property": "https://example.org/property/RAUM", "label": "quartier"},
    {"property": "https://example.org/property/unrelated", "label": "something_else"},
]


class TestSPARQLLookupProperties:
    def _make_lookup(self, rows):
        lookup = SPARQLLookup("https://sparql.example.org/query")
        lookup._sparql_query = MagicMock(return_value=rows)
        return lookup

    def test_exact_label_match(self):
        lookup = self._make_lookup(PROPERTY_ROWS)
        context = lookup.build_reuse_context(SAMPLE_CSV_SCHEMA)
        # "JAHR" matches label "JAHR" (case-insensitive)
        prop_cols = {p.matched_column for p in context.properties}
        assert "JAHR" in prop_cols

    def test_matched_uri_is_correct(self):
        lookup = self._make_lookup(PROPERTY_ROWS)
        context = lookup.build_reuse_context(SAMPLE_CSV_SCHEMA)
        jahr_match = next(p for p in context.properties if p.matched_column == "JAHR")
        assert jahr_match.existing_uri == "https://example.org/property/ZEIT"

    def test_no_match_for_unrelated_column(self):
        lookup = self._make_lookup(PROPERTY_ROWS)
        context = lookup.build_reuse_context(SAMPLE_CSV_SCHEMA)
        matched_cols = {p.matched_column for p in context.properties}
        assert "ANZAHL" not in matched_cols

    def test_empty_endpoint_result_returns_empty(self):
        lookup = self._make_lookup([])
        context = lookup.build_reuse_context(SAMPLE_CSV_SCHEMA)
        assert context.properties == []

    def test_sparql_error_is_logged_not_raised(self):
        lookup = SPARQLLookup("https://sparql.example.org/query")
        lookup._sparql_query = MagicMock(side_effect=Exception("connection refused"))
        # Should not raise — returns empty context
        context = lookup.build_reuse_context(SAMPLE_CSV_SCHEMA)
        assert context.properties == []


# ---------------------------------------------------------------------------
# SPARQLLookup — DefinedTermSet lookup
# ---------------------------------------------------------------------------

DEFINED_TERM_ROWS = [
    {
        "termSet": "https://example.org/ts/quartier",
        "term": "https://example.org/code/Kreis 1",
        "name": "Kreis 1",
    },
    {
        "termSet": "https://example.org/ts/quartier",
        "term": "https://example.org/code/Kreis 2",
        "name": "Kreis 2",
    },
    {
        "termSet": "https://example.org/ts/quartier",
        "term": "https://example.org/code/Kreis 3",
        "name": "Kreis 3",
    },
]


class TestSPARQLLookupDefinedTermSets:
    def _make_lookup(self, rows):
        lookup = SPARQLLookup("https://sparql.example.org/query")
        lookup._sparql_query = MagicMock(return_value=rows)
        return lookup

    def test_full_coverage_match(self):
        lookup = self._make_lookup(DEFINED_TERM_ROWS)
        context = lookup.build_reuse_context(SAMPLE_CSV_SCHEMA, SAMPLE_MAPPING_PROPOSAL)
        dts_cols = {d.matched_column for d in context.defined_term_sets}
        assert "QUARTIER" in dts_cols

    def test_uri_template_detected(self):
        lookup = self._make_lookup(DEFINED_TERM_ROWS)
        context = lookup.build_reuse_context(SAMPLE_CSV_SCHEMA, SAMPLE_MAPPING_PROPOSAL)
        quartier = next(d for d in context.defined_term_sets if d.matched_column == "QUARTIER")
        assert quartier.uri_template == "https://example.org/code/$(QUARTIER)~iri"

    def test_coverage_value(self):
        lookup = self._make_lookup(DEFINED_TERM_ROWS)
        context = lookup.build_reuse_context(SAMPLE_CSV_SCHEMA, SAMPLE_MAPPING_PROPOSAL)
        quartier = next(d for d in context.defined_term_sets if d.matched_column == "QUARTIER")
        assert quartier.coverage == pytest.approx(1.0)

    def test_below_min_coverage_not_matched(self):
        # Only one of three values matches → coverage = 1/3 < MIN_COVERAGE
        partial_rows = [DEFINED_TERM_ROWS[0]]
        lookup = self._make_lookup(partial_rows)
        context = lookup.build_reuse_context(SAMPLE_CSV_SCHEMA, SAMPLE_MAPPING_PROPOSAL)
        assert context.defined_term_sets == []

    def test_no_result_returns_empty(self):
        lookup = self._make_lookup([])
        context = lookup.build_reuse_context(SAMPLE_CSV_SCHEMA, SAMPLE_MAPPING_PROPOSAL)
        assert context.defined_term_sets == []

    def test_uri_suffix_mismatch_skipped(self):
        # URI does NOT end with the schema:name value → no template derivable
        rows = [
            {
                "termSet": "https://example.org/ts/quartier",
                "term": "https://example.org/code/Q1",  # suffix Q1 ≠ "Kreis 1"
                "name": "Kreis 1",
            },
            {
                "termSet": "https://example.org/ts/quartier",
                "term": "https://example.org/code/Q2",
                "name": "Kreis 2",
            },
            {
                "termSet": "https://example.org/ts/quartier",
                "term": "https://example.org/code/Q3",
                "name": "Kreis 3",
            },
        ]
        lookup = self._make_lookup(rows)
        context = lookup.build_reuse_context(SAMPLE_CSV_SCHEMA, SAMPLE_MAPPING_PROPOSAL)
        assert context.defined_term_sets == []


# ---------------------------------------------------------------------------
# SPARQLLookup — no endpoint (passthrough)
# ---------------------------------------------------------------------------

class TestSPARQLLookupNoEndpoint:
    def test_no_endpoint_skipped_in_lookup_node(self):
        """lookup_node skips SPARQL when no endpoint is configured."""
        from ogd_to_lod.config import Config, AzureOpenAIConfig, GitHubConfig, SPARQLConfig
        from ogd_to_lod.graph.nodes import lookup_node
        from ogd_to_lod.graph.state import FlowState, GraphState

        config = Config(
            github=GitHubConfig(repo="org/repo", token="tok"),
            azure=AzureOpenAIConfig(endpoint="https://e", api_key="k", deployment="d"),
            sparql=SPARQLConfig(endpoint=None),
        )
        state = GraphState(
            csv_schema=SAMPLE_CSV_SCHEMA,
        )
        result = lookup_node(state, config)

        assert result.current_state == FlowState.PROPOSE
        assert result.reuse_context is not None
        assert not result.reuse_context.has_matches()
        assert not result.awaiting_user_input
