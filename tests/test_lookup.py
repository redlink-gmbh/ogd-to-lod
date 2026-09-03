"""Tests for SPARQL-based vocabulary reuse lookup."""
from collections import Counter
from unittest.mock import MagicMock, patch

import pytest
from ogd_to_lod.lookup.property_matcher import PropertyMatcher
from ogd_to_lod.config import SPARQLConfig, MappingTemplateConfig
from ogd_to_lod.lookup import ReuseContext
from ogd_to_lod.lookup.models import ColumnReuse, MatchedProperty
from ogd_to_lod.lookup.term_matcher import TermMatcher
from ogd_to_lod.lookup.csv_values import CSVValues


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
            columns=[
                ColumnReuse(
                    column="QUARTIER",
                    term_set_uri="https://example.org/code/quartier/",
                    coverage=1.0,
                    distinct_coverage=1.0,
                    uri_template="https://example.org/code/$(QUARTIER)~iri",
                    template_verified=True,
                    value_to_term={"quartier1": "https://example.org/code/quartier/quartier1"},
                    unmatched_values=[],
                    truncated=False,
                    property=None,
                )
            ]
        )
        assert ctx.has_matches()

    def test_to_prompt_text_empty(self):
        assert ReuseContext().to_prompt_text() == ""

    # def test_to_prompt_text_with_property(self):
    #     ctx = ReuseContext(
    #         properties=[
    #             MatchedProperty(
    #                 existing_uri="https://example.org/property/ZEIT",
    #                 label="Zeit",
    #                 matched_column="JAHR",
    #             )
    #         ]
    #     )
    #     text = ctx.to_prompt_text()
    #     assert "https://example.org/property/ZEIT" in text
    #     assert "JAHR" in text
    #     assert "Properties" in text

    def test_to_prompt_text_with_defined_term_set(self):
        ctx = ReuseContext(
            columns=[
                ColumnReuse(
                    column="QUARTIER",
                    term_set_uri="https://example.org/code/quartier/",
                    coverage=1.0,
                    distinct_coverage=1.0,
                    uri_template="https://example.org/code/$(QUARTIER)~iri",
                    template_verified=True,
                    value_to_term={"quartier1": "https://example.org/code/quartier/quartier1"},
                    unmatched_values=[],
                    truncated=False,
                    property=None,
                )
            ]
        )
        text = ctx.to_prompt_text()
        assert "https://example.org/code/$(QUARTIER)~iri" in text
        assert "QUARTIER" in text
        assert "DefinedTermSets" in text
        assert "100%" in text

    def test_to_prompt_text_instructs_no_separate_mapping(self):
        ctx = ReuseContext(
            columns=[
                ColumnReuse(
                    column="QUARTIER",
                    term_set_uri="https://example.org/code/quartier/",
                    coverage=1.0,
                    distinct_coverage=1.0,
                    uri_template="https://example.org/code/$(QUARTIER)~iri",
                    template_verified=True,
                    value_to_term={"quartier1": "https://example.org/code/quartier/quartier1"},
                    unmatched_values=[],
                    truncated=False,
                    property=None,
                )
            ]
        )
        assert "Do NOT generate a separate mapping" in ctx.to_prompt_text()

    def test_to_display_text_empty(self):
        assert ReuseContext().to_display_text() == ""


# ---------------------------------------------------------------------------
# SPARQLLookup — property lookup
# ---------------------------------------------------------------------------

def _column_reuse(column: str, value_to_term: dict[str, str]) -> ColumnReuse:
    return ColumnReuse(
        column=column,
        term_set_uri="https://example.org/ts/dummy",
        coverage=1.0,
        distinct_coverage=1.0,
        uri_template=None,
        template_verified=False,
        value_to_term=value_to_term,
        unmatched_values=[],
        truncated=False,
        property=None,
    )

class TestPropertyMatcher:
    def _make_ai_service(self, yaml_blocks):
        ai_service = MagicMock()
        ai_service.ask_once.return_value = "irrelevant raw reply"
        return ai_service, yaml_blocks

    @patch("ogd_to_lod.lookup.property_matcher.AIService.parse_response")
    @patch("ogd_to_lod.lookup.property_matcher.sparql_query")
    def test_confirmed_match_is_selected(self, mock_sparql_query, mock_parse_response):
        column = _column_reuse("JAHR", {"2020": "https://example.org/code/2020"})
        mock_sparql_query.return_value = [
            {"property": "https://example.org/property/ZEIT", "label": "Zeit", "usageCount": "5"},
        ]
        mock_parse_response.return_value.get_yaml_blocks.return_value = [
            """
            matches:
              - column: JAHR
                property: https://example.org/property/ZEIT
                match: true
            """
        ]
        ai_service = MagicMock(ask_once=MagicMock(return_value="reply"))

        matcher = PropertyMatcher()
        result = matcher.match_properties([column], ai_service, "https://sparql.example.org/query")

        assert len(result) == 1
        assert result[0].existing_uri == "https://example.org/property/ZEIT"
        assert result[0].matched_column == "JAHR"
        assert result[0].usage_count == 5
        assert column.property is result[0]  # column mutated in place

    @patch("ogd_to_lod.lookup.property_matcher.AIService.parse_response")
    @patch("ogd_to_lod.lookup.property_matcher.sparql_query")
    def test_highest_usage_count_wins_among_confirmed(self, mock_sparql_query, mock_parse_response):
        column = _column_reuse("JAHR", {"2020": "https://example.org/code/2020"})
        mock_sparql_query.return_value = [
            {"property": "https://example.org/property/A", "label": "A", "usageCount": "3"},
            {"property": "https://example.org/property/B", "label": "B", "usageCount": "9"},
        ]
        mock_parse_response.return_value.get_yaml_blocks.return_value = [
            """
            matches:
              - column: JAHR
                property: https://example.org/property/A
                match: true
              - column: JAHR
                property: https://example.org/property/B
                match: true
            """
        ]
        ai_service = MagicMock(ask_once=MagicMock(return_value="reply"))

        matcher = PropertyMatcher()
        result = matcher.match_properties([column], ai_service, "https://sparql.example.org/query")

        assert len(result) == 1
        assert result[0].existing_uri == "https://example.org/property/B"

    @patch("ogd_to_lod.lookup.property_matcher.AIService.parse_response")
    @patch("ogd_to_lod.lookup.property_matcher.sparql_query")
    def test_no_llm_confirmation_means_no_property(self, mock_sparql_query, mock_parse_response):
        column = _column_reuse("JAHR", {"2020": "https://example.org/code/2020"})
        mock_sparql_query.return_value = [
            {"property": "https://example.org/property/ZEIT", "label": "Zeit", "usageCount": "5"},
        ]
        mock_parse_response.return_value.get_yaml_blocks.return_value = [
            """
            matches:
              - column: JAHR
                property: https://example.org/property/ZEIT
                match: false
            """
        ]
        ai_service = MagicMock(ask_once=MagicMock(return_value="reply"))

        matcher = PropertyMatcher()
        result = matcher.match_properties([column], ai_service, "https://sparql.example.org/query")

        assert result == []
        assert column.property is None

    @patch("ogd_to_lod.lookup.property_matcher.AIService.parse_response")
    @patch("ogd_to_lod.lookup.property_matcher.sparql_query")
    def test_column_with_no_terms_is_never_queried(self, mock_sparql_query, mock_parse_response):

        measure_column = _column_reuse("ANZAHL", {})
        mock_parse_response.return_value.get_yaml_blocks.return_value = []
        ai_service = MagicMock(ask_once=MagicMock(return_value="reply"))

        matcher = PropertyMatcher()
        result = matcher.match_properties([measure_column], ai_service, "https://sparql.example.org/query")

        mock_sparql_query.assert_not_called()
        assert result == []

    @patch("ogd_to_lod.lookup.property_matcher.AIService.parse_response")
    @patch("ogd_to_lod.lookup.property_matcher.sparql_query")
    def test_single_batched_llm_call_for_multiple_columns(self, mock_sparql_query, mock_parse_response):
        jahr = _column_reuse("JAHR", {"2020": "https://example.org/code/2020"})
        quartier = _column_reuse("QUARTIER", {"Kreis 1": "https://example.org/code/kreis1"})
        mock_sparql_query.return_value = [
            {"property": "https://example.org/property/X", "label": "X", "usageCount": "1"},
        ]
        mock_parse_response.return_value.get_yaml_blocks.return_value = []
        ai_service = MagicMock(ask_once=MagicMock(return_value="reply"))

        matcher = PropertyMatcher()
        matcher.match_properties([jahr, quartier], ai_service, "https://sparql.example.org/query")

        ai_service.ask_once.assert_called_once()

    @patch("ogd_to_lod.lookup.property_matcher.AIService.parse_response")
    @patch("ogd_to_lod.lookup.property_matcher.sparql_query")
    def test_empty_sparql_result_skips_column(self, mock_sparql_query, mock_parse_response):
        column = _column_reuse("JAHR", {"2020": "https://example.org/code/2020"})
        mock_sparql_query.return_value = []
        mock_parse_response.return_value.get_yaml_blocks.return_value = []
        ai_service = MagicMock(ask_once=MagicMock(return_value="reply"))

        matcher = PropertyMatcher()
        result = matcher.match_properties([column], ai_service, "https://sparql.example.org/query")

        assert result == []

    @patch("ogd_to_lod.lookup.property_matcher.AIService.parse_response")
    @patch("ogd_to_lod.lookup.property_matcher.sparql_query")
    def test_no_reused_columns_returns_empty_without_llm_call(self, mock_sparql_query, mock_parse_response):
        ai_service = MagicMock(ask_once=MagicMock(return_value="reply"))
        matcher = PropertyMatcher()

        result = matcher.match_properties([], ai_service, "https://sparql.example.org/query")

        assert result == []
        mock_sparql_query.assert_not_called()



# ---------------------------------------------------------------------------
# SPARQLLookup — DefinedTermSet lookup
# ---------------------------------------------------------------------------

SAMPLE_TERM_ROWS = [
    {"termSet": "https://example.org/ts/quartier", "term": "https://example.org/code/Kreis 1", "name": "Kreis 1"},
    {"termSet": "https://example.org/ts/quartier", "term": "https://example.org/code/Kreis 2", "name": "Kreis 2"},
    {"termSet": "https://example.org/ts/quartier", "term": "https://example.org/code/Kreis 3", "name": "Kreis 3"},
]

SAMPLE_TERMSET_MEMBERS = [
    {"term": "https://example.org/code/Kreis 1", "name": "Kreis 1"},
    {"term": "https://example.org/code/Kreis 2", "name": "Kreis 2"},
    {"term": "https://example.org/code/Kreis 3", "name": "Kreis 3"},
]

# NOTE: TermMatcher.match_terms() expects a CSV schema shaped for
# get_column_values() (source/encoding/delimiter), which is a different
# shape than SAMPLE_CSV_SCHEMA above (used by the old property-matching
# code and TestSPARQLLookupNoEndpoint). Kept separate on purpose so the
# two don't get silently conflated again.
TERM_MATCHER_CSV_SCHEMA = {
    "source": "quartiere.csv",
    "encoding": "utf-8",
    "delimiter": ",",
}

TERM_MATCHER_MAPPING_PROPOSAL = {
    "dimensions": [{"column": "QUARTIER", "type": "categorical"}],
}


class TestTermMatcherDefinedTermSets:
    def _make_matcher(self):
        config = SPARQLConfig(
            endpoint="https://sparql.example.org/query",
            sample_size=8,
            sample_threshold=0.5,
            max_candidate_term_sets=3,
            min_row_coverage=0.9,
        )
        return TermMatcher(config, "https://sparql.example.org/query")

    def _mock_csv_values(self, counter=None, total_rows=10, truncated=None):
        if counter is None:
            counter = Counter({"Kreis 1": 5, "Kreis 2": 3, "Kreis 3": 2})
        return CSVValues(
            columns={"QUARTIER": counter},
            total_rows=total_rows,
            truncated=truncated or set(),
        )

    @patch("ogd_to_lod.lookup.term_matcher.get_column_values")
    @patch("ogd_to_lod.lookup.term_matcher.sparql_query")
    def test_full_coverage_match(self, mock_sparql_query, mock_get_column_values):
        mock_get_column_values.return_value = self._mock_csv_values()
        # 1st call = sample_terms_query, 2nd call = term_set_query
        mock_sparql_query.side_effect = [SAMPLE_TERM_ROWS, SAMPLE_TERMSET_MEMBERS]

        matcher = self._make_matcher()
        results = matcher.match_terms(TERM_MATCHER_CSV_SCHEMA, TERM_MATCHER_MAPPING_PROPOSAL)

        cols = {r.column for r in results}
        assert "QUARTIER" in cols

    @patch("ogd_to_lod.lookup.term_matcher.get_column_values")
    @patch("ogd_to_lod.lookup.term_matcher.sparql_query")
    def test_value_to_term_mapping_and_coverage(self, mock_sparql_query, mock_get_column_values):
        mock_get_column_values.return_value = self._mock_csv_values()
        mock_sparql_query.side_effect = [SAMPLE_TERM_ROWS, SAMPLE_TERMSET_MEMBERS]

        matcher = self._make_matcher()
        results = matcher.match_terms(TERM_MATCHER_CSV_SCHEMA, TERM_MATCHER_MAPPING_PROPOSAL)

        quartier = next(r for r in results if r.column == "QUARTIER")
        assert quartier.term_set_uri == "https://example.org/ts/quartier"
        assert quartier.coverage == 1.0
        assert quartier.value_to_term == {
            "Kreis 1": "https://example.org/code/Kreis 1",
            "Kreis 2": "https://example.org/code/Kreis 2",
            "Kreis 3": "https://example.org/code/Kreis 3",
        }
        assert quartier.unmatched_values == []

    @patch("ogd_to_lod.lookup.term_matcher.get_column_values")
    @patch("ogd_to_lod.lookup.term_matcher.sparql_query")
    def test_coverage_value(self, mock_sparql_query, mock_get_column_values):
        counter = Counter({"Kreis 1": 5, "Kreis 2": 3, "Kreis 3": 2})
        mock_get_column_values.return_value = self._mock_csv_values(counter)
        mock_sparql_query.side_effect = [SAMPLE_TERM_ROWS, SAMPLE_TERMSET_MEMBERS]

        matcher = self._make_matcher()
        results = matcher.match_terms(TERM_MATCHER_CSV_SCHEMA, TERM_MATCHER_MAPPING_PROPOSAL)

        quartier = next(r for r in results if r.column == "QUARTIER")
        assert quartier.coverage == pytest.approx(1.0)

    @patch("ogd_to_lod.lookup.term_matcher.get_column_values")
    @patch("ogd_to_lod.lookup.term_matcher.sparql_query")
    def test_below_min_coverage_not_matched(self, mock_sparql_query, mock_get_column_values):

        counter = Counter({"Kreis 1": 1, "Kreis 2": 1, "Kreis 3": 8})
        mock_get_column_values.return_value = self._mock_csv_values(counter)


        term_set_partial_members = [
            {"term": "https://example.org/code/Kreis 1", "name": "Kreis 1"},
            {"term": "https://example.org/code/Kreis 2", "name": "Kreis 2"},
        ]
        mock_sparql_query.side_effect = [SAMPLE_TERM_ROWS, term_set_partial_members]

        # coverage = 2 / 10 = 0.2, below min_row_coverage=0.9
        matcher = self._make_matcher()
        results = matcher.match_terms(TERM_MATCHER_CSV_SCHEMA, TERM_MATCHER_MAPPING_PROPOSAL)

        assert results == []

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
            mapping=MappingTemplateConfig(),
            sparql=SPARQLConfig(endpoint=None),
        )
        state = GraphState(
            csv_schema=SAMPLE_CSV_SCHEMA,
        )
        result = lookup_node(state, config, MagicMock())

        assert result.current_state == FlowState.GENERATE
        assert result.reuse_context is not None
        assert not result.reuse_context.has_matches()
        assert not result.awaiting_user_input