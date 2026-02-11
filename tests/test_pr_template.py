"""Tests for the PR description template system."""

from pathlib import Path

import pytest

from ogd_to_lod.github.pr_template import (
    _RDF_PREVIEW_MAX_CHARS,
    build_csv_preview_section,
    build_mapping_structure_section,
    build_rdf_preview_section,
    load_pr_template,
    render_pr_template,
)
from ogd_to_lod.graph.state import DimensionProposal, MappingProposal, MeasureProposal


# -- load_pr_template --------------------------------------------------------


class TestLoadPrTemplate:
    """Tests for load_pr_template (now returns str)."""

    def test_loads_from_file(self, tmp_path):
        template_file = tmp_path / "custom.md"
        template_file.write_text("Hello {{Dataset Name}}!")
        result = load_pr_template(template_file)
        assert isinstance(result, str)
        assert "{{Dataset Name}}" in result

    def test_falls_back_to_default_when_file_missing(self):
        result = load_pr_template("/nonexistent/path.md")
        assert "OGD to LOD" in result  # footer from default template

    def test_falls_back_to_default_when_path_is_none(self):
        result = load_pr_template(None)
        assert isinstance(result, str)
        assert "{{Dataset Name}}" in result

    def test_loads_project_template(self):
        """The actual config/pr_template.md file loads correctly."""
        result = load_pr_template(Path("config/pr_template.md"))
        assert isinstance(result, str)
        assert "{{Dataset Name" in result


# -- render_pr_template ------------------------------------------------------


class TestRenderPrTemplate:
    """Tests for the {{placeholder}} rendering engine."""

    def test_inline_with_data(self):
        template = "Name: {{Dataset Name}}"
        result = render_pr_template(template, {"dataset_name": "My Dataset"})
        assert result == "Name: My Dataset"

    def test_inline_with_default_and_no_data(self):
        template = "Name: {{Dataset Name|Fallback}}"
        result = render_pr_template(template, {})
        assert result == "Name: Fallback"

    def test_inline_with_data_overrides_default(self):
        template = "Name: {{Dataset Name|Fallback}}"
        result = render_pr_template(template, {"dataset_name": "Actual"})
        assert result == "Name: Actual"

    def test_inline_missing_data_no_default_gives_empty(self):
        template = "Name: {{Dataset Name}}"
        result = render_pr_template(template, {})
        assert result == "Name: "

    def test_block_replacement_removes_example_content(self):
        template = (
            "### Mapping Structure\n\n"
            "{{Mapping Decisions}}\n\n"
            "**Example dimension line**\n"
            "**Example measure line**\n\n"
            "### Next Section\n"
        )
        result = render_pr_template(
            template, {"mapping_structure": "**Dimensions:**\n- `year` (temporal)"}
        )
        assert "**Dimensions:**" in result
        assert "`year`" in result
        assert "Example dimension line" not in result
        assert "### Next Section" in result

    def test_block_without_data_keeps_example_content(self):
        template = (
            "### Mapping Structure\n\n"
            "{{Mapping Decisions}}\n\n"
            "**Example line**\n\n"
            "### Next Section\n"
        )
        result = render_pr_template(template, {})
        assert "Example line" in result
        assert "### Next Section" in result

    def test_unregistered_placeholder_with_default(self):
        template = "Value: {{Unknown Thing|default_val}}"
        result = render_pr_template(template, {})
        assert result == "Value: default_val"

    def test_unregistered_placeholder_without_default_stays(self):
        template = "Value: {{Unknown Thing}}"
        result = render_pr_template(template, {})
        assert result == "Value: {{Unknown Thing}}"

    def test_multiple_inline_placeholders(self):
        template = "{{Dataset Name}} - {{Base URI}}"
        result = render_pr_template(
            template, {"dataset_name": "Test", "base_uri": "`https://example.org/`"}
        )
        assert result == "Test - `https://example.org/`"


# -- build_mapping_structure_section -----------------------------------------


class TestBuildMappingStructureSection:
    """Tests for build_mapping_structure_section."""

    def test_with_dimensions_and_measures(self):
        proposal = MappingProposal(
            dimensions=[
                DimensionProposal(column="year", dimension_type="temporal", granularity="year"),
            ],
            measures=[
                MeasureProposal(column="value", unit="count", aggregation="sum"),
            ],
        )
        result = build_mapping_structure_section(proposal)
        assert "`year`" in result
        assert "temporal" in result
        assert "granularity: year" in result
        assert "`value`" in result
        assert "(count)" in result
        assert "aggregation: sum" in result
        # Should NOT contain the heading (it's in the template now)
        assert "### Mapping Structure" not in result

    def test_dimensions_only(self):
        proposal = MappingProposal(
            dimensions=[DimensionProposal(column="region", dimension_type="spatial")],
        )
        result = build_mapping_structure_section(proposal)
        assert "`region`" in result
        assert "Measures" not in result

    def test_measures_only(self):
        proposal = MappingProposal(
            measures=[MeasureProposal(column="count")],
        )
        result = build_mapping_structure_section(proposal)
        assert "`count`" in result

    def test_empty_when_none(self):
        assert build_mapping_structure_section(None) == ""

    def test_empty_proposal(self):
        assert build_mapping_structure_section(MappingProposal()) == ""

    def test_dimension_with_hierarchy(self):
        proposal = MappingProposal(
            dimensions=[
                DimensionProposal(
                    column="city",
                    dimension_type="spatial",
                    hierarchy="geography",
                ),
            ],
        )
        result = build_mapping_structure_section(proposal)
        assert "hierarchy: geography" in result

    def test_with_mapping_decisions(self):
        proposal = MappingProposal(
            dimensions=[
                DimensionProposal(column="year", dimension_type="temporal"),
            ],
        )
        result = build_mapping_structure_section(proposal, mapping_decisions="Year was chosen as temporal.")
        assert "Year was chosen as temporal." in result
        assert "`year`" in result

    def test_without_mapping_decisions(self):
        proposal = MappingProposal(
            dimensions=[
                DimensionProposal(column="year", dimension_type="temporal"),
            ],
        )
        result = build_mapping_structure_section(proposal)
        assert "`year`" in result


# -- build_rdf_preview_section -----------------------------------------------


class TestBuildRdfPreviewSection:
    """Tests for build_rdf_preview_section."""

    def test_with_short_preview(self):
        rdf = "@prefix ex: <https://example.org/> .\nex:a ex:b ex:c ."
        result = build_rdf_preview_section(rdf)
        assert "```turtle" in result
        assert "ex:a ex:b ex:c" in result
        assert "truncated" not in result
        # Should NOT contain the heading (it's in the template now)
        assert "### RDF Preview" not in result

    def test_truncates_long_preview(self):
        rdf = "x" * (_RDF_PREVIEW_MAX_CHARS + 500)
        result = build_rdf_preview_section(rdf)
        assert "truncated" in result
        # Content should be at most _RDF_PREVIEW_MAX_CHARS
        code_start = result.index("```turtle\n") + len("```turtle\n")
        code_end = result.index("\n```")
        content = result[code_start:code_end]
        # Content includes truncation notice
        assert len(content) <= _RDF_PREVIEW_MAX_CHARS + len("\n... (truncated)")

    def test_empty_when_none(self):
        assert build_rdf_preview_section(None) == ""

    def test_empty_when_empty_string(self):
        assert build_rdf_preview_section("") == ""


# -- build_csv_preview_section -----------------------------------------------


class TestBuildCsvPreviewSection:
    """Tests for build_csv_preview_section."""

    def test_normal_case(self):
        schema = {
            "columns": [
                {"name": "year", "type": "int"},
                {"name": "region", "type": "str"},
                {"name": "value", "type": "float"},
            ],
            "sample_rows": [
                {"year": 2020, "region": "North", "value": 100.5},
                {"year": 2021, "region": "South", "value": 200.3},
            ],
        }
        result = build_csv_preview_section(schema)
        assert "| year | region | value |" in result
        assert "| --- | --- | --- |" in result
        assert "| 2020 | North | 100.5 |" in result
        assert "| 2021 | South | 200.3 |" in result

    def test_empty_schema(self):
        assert build_csv_preview_section(None) == ""
        assert build_csv_preview_section({}) == ""

    def test_no_sample_rows(self):
        schema = {
            "columns": [{"name": "year", "type": "int"}],
            "sample_rows": [],
        }
        assert build_csv_preview_section(schema) == ""

    def test_no_columns(self):
        schema = {"columns": [], "sample_rows": [{"year": 2020}]}
        assert build_csv_preview_section(schema) == ""


# -- Full template substitution -----------------------------------------------


class TestFullTemplateSubstitution:
    """End-to-end template rendering."""

    def test_all_sections_populated(self):
        template = load_pr_template(None)
        result = render_pr_template(
            template,
            {
                "dataset_name": "population",
                "dataset_description": "Population dataset",
                "csv_source": "`pop.csv`",
                "dcat_source": "`meta.jsonld`",
                "base_uri": "`https://example.org/`",
                "mapping_structure": "**Dimensions:**\n- `year` (temporal)",
                "csv_preview": "| year |\n| --- |\n| 2020 |",
                "rdf_preview": "```turtle\nex:a ex:b ex:c .\n```",
            },
        )
        assert "population" in result
        assert "Population dataset" in result
        assert "`pop.csv`" in result
        assert "`meta.jsonld`" in result
        assert "`https://example.org/`" in result
        assert "`year`" in result
        assert "ex:a ex:b ex:c" in result
        assert "OGD to LOD" in result

    def test_empty_sections_produce_clean_output(self):
        template = load_pr_template(None)
        result = render_pr_template(
            template,
            {
                "dataset_name": "empty-test",
                "dataset_description": "",
                "csv_source": "",
                "dcat_source": "",
                "base_uri": "",
                "mapping_structure": "",
                "csv_preview": "",
                "rdf_preview": "",
            },
        )
        assert "empty-test" in result
        # Block placeholders with empty string values should still replace
        # (no leftover {{}} tokens for registered placeholders)
