"""Tests for the PR description template system."""

import tempfile
from pathlib import Path
from string import Template

import pytest

from ogd_to_lod.github.pr_template import (
    _RDF_PREVIEW_MAX_CHARS,
    build_data_sources_section,
    build_dcat_section,
    build_mapping_structure_section,
    build_rdf_preview_section,
    load_pr_template,
)
from ogd_to_lod.graph.state import DimensionProposal, MappingProposal, MeasureProposal


# -- load_pr_template --------------------------------------------------------


class TestLoadPrTemplate:
    """Tests for load_pr_template."""

    def test_loads_from_file(self, tmp_path):
        template_file = tmp_path / "custom.md"
        template_file.write_text("Hello ${mapping_name}!")
        tmpl = load_pr_template(template_file)
        assert isinstance(tmpl, Template)
        assert tmpl.safe_substitute(mapping_name="test") == "Hello test!"

    def test_falls_back_to_default_when_file_missing(self):
        tmpl = load_pr_template("/nonexistent/path.md")
        result = tmpl.safe_substitute(mapping_name="fallback")
        assert "fallback" in result
        assert "OGD to LOD" in result  # footer from default template

    def test_falls_back_to_default_when_path_is_none(self):
        tmpl = load_pr_template(None)
        result = tmpl.safe_substitute(mapping_name="test")
        assert "test" in result

    def test_loads_project_template(self):
        """The actual config/pr_template.md file loads correctly."""
        tmpl = load_pr_template(Path("config/pr_template.md"))
        result = tmpl.safe_substitute(mapping_name="demo")
        assert "demo" in result


# -- build_data_sources_section -----------------------------------------------


class TestBuildDataSourcesSection:
    """Tests for build_data_sources_section."""

    def test_all_fields(self):
        result = build_data_sources_section(
            csv_path="/data/file.csv",
            dcat_path="/data/meta.jsonld",
            base_uri="https://example.org/",
        )
        assert "`/data/file.csv`" in result
        assert "`/data/meta.jsonld`" in result
        assert "`https://example.org/`" in result

    def test_csv_only(self):
        result = build_data_sources_section(csv_path="/data/file.csv")
        assert "CSV Source" in result
        assert "DCAT" not in result

    def test_empty_when_no_data(self):
        result = build_data_sources_section()
        assert result == ""


# -- build_dcat_section -------------------------------------------------------


class TestBuildDcatSection:
    """Tests for build_dcat_section."""

    def test_with_title_and_description(self):
        result = build_dcat_section({"title": "My Dataset", "description": "Short desc"})
        assert "My Dataset" in result
        assert "Short desc" in result

    def test_truncates_long_description(self):
        long_desc = "x" * 300
        result = build_dcat_section({"title": "T", "description": long_desc})
        assert "..." in result
        # Should not contain the full 300-char string
        assert long_desc not in result

    def test_empty_when_none(self):
        assert build_dcat_section(None) == ""

    def test_empty_when_empty_dict(self):
        assert build_dcat_section({}) == ""


# -- build_mapping_structure_section ------------------------------------------


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


# -- build_rdf_preview_section ------------------------------------------------


class TestBuildRdfPreviewSection:
    """Tests for build_rdf_preview_section."""

    def test_with_short_preview(self):
        rdf = "@prefix ex: <https://example.org/> .\nex:a ex:b ex:c ."
        result = build_rdf_preview_section(rdf)
        assert "```turtle" in result
        assert "ex:a ex:b ex:c" in result
        assert "truncated" not in result

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


# -- Full template substitution -----------------------------------------------


class TestFullTemplateSubstitution:
    """End-to-end template rendering."""

    def test_all_sections_populated(self):
        tmpl = load_pr_template(None)
        result = tmpl.safe_substitute(
            mapping_name="population",
            data_sources="**CSV Source:** `pop.csv`",
            dcat_section="**Dataset Title:** Population",
            mapping_structure="### Mapping Structure\n**Dimensions:**\n- `year` (temporal)",
            rdf_preview_section="### RDF Preview\n```turtle\nex:a ex:b ex:c .\n```",
        )
        assert "## RML Mapping: population" in result
        assert "`pop.csv`" in result
        assert "Population" in result
        assert "`year`" in result
        assert "ex:a ex:b ex:c" in result
        assert "OGD to LOD" in result

    def test_safe_substitute_with_undefined_variables(self):
        """safe_substitute leaves undefined ${vars} intact."""
        tmpl = load_pr_template(None)
        result = tmpl.safe_substitute(mapping_name="test")
        # Undefined placeholders should remain as-is
        assert "${data_sources}" in result

    def test_empty_sections_produce_clean_output(self):
        tmpl = load_pr_template(None)
        result = tmpl.safe_substitute(
            mapping_name="empty-test",
            data_sources="",
            dcat_section="",
            mapping_structure="",
            rdf_preview_section="",
        )
        assert "empty-test" in result
        # Should not have leftover ${} placeholders
        assert "${" not in result
