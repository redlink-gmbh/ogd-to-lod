"""Tests for CSV and DCAT parsers."""

import json
from pathlib import Path

import pytest

from ogd_to_lod.parsers import (
    ColumnType,
    CSVData,
    CSVParseError,
    DCATMetadata,
    DCATParseError,
    ParsedInput,
    dcat_format_to_extension,
    parse_csv,
    parse_dcat,
)


class TestCSVParser:
    """Tests for CSV parser."""

    def test_parse_simple_csv(self, tmp_path: Path) -> None:
        """Test parsing a simple CSV file."""
        csv_content = """name,age,salary
Alice,30,50000.50
Bob,25,45000.75
Charlie,35,60000.00"""
        csv_file = tmp_path / "simple.csv"
        csv_file.write_text(csv_content)

        result = parse_csv(str(csv_file))

        assert isinstance(result, CSVData)
        assert result.source == str(csv_file)
        assert len(result.columns) == 3
        assert result.total_rows == 3
        assert result.encoding in ("utf-8", "utf-8-sig")
        assert result.delimiter == ","

    def test_column_names_extracted(self, tmp_path: Path) -> None:
        """Test that column names are correctly extracted."""
        csv_content = """first_name,last_name,email
John,Doe,john@example.com"""
        csv_file = tmp_path / "columns.csv"
        csv_file.write_text(csv_content)

        result = parse_csv(str(csv_file))

        assert result.column_names() == ["first_name", "last_name", "email"]

    def test_detect_integer_type(self, tmp_path: Path) -> None:
        """Test detection of integer column type."""
        csv_content = """id,count
1,100
2,200
3,300"""
        csv_file = tmp_path / "integers.csv"
        csv_file.write_text(csv_content)

        result = parse_csv(str(csv_file))

        assert result.columns[0].detected_type == ColumnType.INTEGER
        assert result.columns[1].detected_type == ColumnType.INTEGER

    def test_detect_float_type(self, tmp_path: Path) -> None:
        """Test detection of float column type."""
        csv_content = """price,rate
19.99,0.05
29.99,0.10
39.99,0.15"""
        csv_file = tmp_path / "floats.csv"
        csv_file.write_text(csv_content)

        result = parse_csv(str(csv_file))

        assert result.columns[0].detected_type == ColumnType.FLOAT
        assert result.columns[1].detected_type == ColumnType.FLOAT

    def test_detect_date_type(self, tmp_path: Path) -> None:
        """Test detection of date column type."""
        csv_content = """date_iso,date_eu
2024-01-15,15.01.2024
2024-02-20,20.02.2024
2024-03-25,25.03.2024"""
        csv_file = tmp_path / "dates.csv"
        csv_file.write_text(csv_content)

        result = parse_csv(str(csv_file))

        assert result.columns[0].detected_type == ColumnType.DATE
        assert result.columns[1].detected_type == ColumnType.DATE

    def test_detect_string_type(self, tmp_path: Path) -> None:
        """Test detection of string column type."""
        csv_content = """name,city
Alice,Zurich
Bob,Geneva
Charlie,Basel"""
        csv_file = tmp_path / "strings.csv"
        csv_file.write_text(csv_content)

        result = parse_csv(str(csv_file))

        assert result.columns[0].detected_type == ColumnType.STRING
        assert result.columns[1].detected_type == ColumnType.STRING

    def test_sample_rows_extracted(self, tmp_path: Path) -> None:
        """Test that sample rows are correctly extracted."""
        csv_content = """name,value
row1,1
row2,2
row3,3
row4,4
row5,5
row6,6"""
        csv_file = tmp_path / "rows.csv"
        csv_file.write_text(csv_content)

        result = parse_csv(str(csv_file), sample_rows=3)

        assert len(result.sample_rows) == 3
        assert result.sample_rows[0]["name"] == "row1"
        assert result.sample_rows[2]["name"] == "row3"
        assert result.total_rows == 6

    def test_default_sample_rows_is_five(self, tmp_path: Path) -> None:
        """Test that default sample rows is 5."""
        csv_content = """n\n""" + "\n".join(str(i) for i in range(10))
        csv_file = tmp_path / "ten_rows.csv"
        csv_file.write_text(csv_content)

        result = parse_csv(str(csv_file))

        assert len(result.sample_rows) == 5

    def test_detect_semicolon_delimiter(self, tmp_path: Path) -> None:
        """Test detection of semicolon delimiter."""
        csv_content = """name;age;city
Alice;30;Zurich
Bob;25;Geneva"""
        csv_file = tmp_path / "semicolon.csv"
        csv_file.write_text(csv_content)

        result = parse_csv(str(csv_file))

        assert result.delimiter == ";"
        assert result.column_names() == ["name", "age", "city"]

    def test_explicit_delimiter(self, tmp_path: Path) -> None:
        """Test using an explicit delimiter."""
        csv_content = """name|age|city
Alice|30|Zurich"""
        csv_file = tmp_path / "pipe.csv"
        csv_file.write_text(csv_content)

        result = parse_csv(str(csv_file), delimiter="|")

        assert result.delimiter == "|"
        assert result.column_names() == ["name", "age", "city"]

    def test_handle_utf8_encoding(self, tmp_path: Path) -> None:
        """Test handling UTF-8 encoded content."""
        csv_content = """name,city
Müller,Zürich
Böhm,Genève"""
        csv_file = tmp_path / "utf8.csv"
        csv_file.write_text(csv_content, encoding="utf-8")

        result = parse_csv(str(csv_file))

        assert result.encoding in ("utf-8", "utf-8-sig")
        assert result.sample_rows[0]["name"] == "Müller"
        assert result.sample_rows[0]["city"] == "Zürich"

    def test_handle_iso88591_encoding(self, tmp_path: Path) -> None:
        """Test handling ISO-8859-1 encoded content."""
        csv_content = """name,city
Müller,Zürich
Böhm,Genève"""
        csv_file = tmp_path / "iso.csv"
        csv_file.write_bytes(csv_content.encode("iso-8859-1"))

        result = parse_csv(str(csv_file))

        assert result.encoding == "iso-8859-1"
        assert result.sample_rows[0]["name"] == "Müller"

    def test_explicit_encoding(self, tmp_path: Path) -> None:
        """Test using an explicit encoding."""
        csv_content = """name\ntest"""
        csv_file = tmp_path / "explicit.csv"
        csv_file.write_text(csv_content, encoding="utf-8")

        result = parse_csv(str(csv_file), encoding="utf-8")

        assert result.encoding in ("utf-8", "utf-8-sig")

    def test_file_not_found_error(self) -> None:
        """Test error when file does not exist."""
        with pytest.raises(CSVParseError, match="File not found"):
            parse_csv("/nonexistent/path/file.csv")

    def test_empty_file_error(self, tmp_path: Path) -> None:
        """Test error when file has header but no data."""
        csv_file = tmp_path / "header_only.csv"
        csv_file.write_text("col1,col2,col3")

        with pytest.raises(CSVParseError, match="no data rows"):
            parse_csv(str(csv_file))

    def test_no_header_error(self, tmp_path: Path) -> None:
        """Test error when file is completely empty."""
        csv_file = tmp_path / "empty.csv"
        csv_file.write_text("")

        with pytest.raises(CSVParseError, match="no header row"):
            parse_csv(str(csv_file))

    def test_sample_values_in_columns(self, tmp_path: Path) -> None:
        """Test that sample values are stored in column info."""
        csv_content = """value
10
20
30"""
        csv_file = tmp_path / "values.csv"
        csv_file.write_text(csv_content)

        result = parse_csv(str(csv_file))

        assert result.columns[0].sample_values == [10, 20, 30]

    def test_column_types_method(self, tmp_path: Path) -> None:
        """Test the column_types method."""
        csv_content = """name,age
Alice,30"""
        csv_file = tmp_path / "types.csv"
        csv_file.write_text(csv_content)

        result = parse_csv(str(csv_file))
        types = result.column_types()

        assert "name" in types
        assert "age" in types
        assert types["name"] == ColumnType.STRING
        assert types["age"] == ColumnType.INTEGER

    def test_mixed_types_default_to_string(self, tmp_path: Path) -> None:
        """Test that mixed type columns default to string."""
        csv_content = """value
10
hello
20
world
30"""
        csv_file = tmp_path / "mixed.csv"
        csv_file.write_text(csv_content)

        result = parse_csv(str(csv_file))

        # Less than 80% are integers, so should be string
        assert result.columns[0].detected_type == ColumnType.STRING

    def test_empty_values_handled(self, tmp_path: Path) -> None:
        """Test that empty values are handled correctly."""
        csv_content = """name,value
Alice,10
Bob,
Charlie,30"""
        csv_file = tmp_path / "empty_values.csv"
        csv_file.write_text(csv_content)

        result = parse_csv(str(csv_file))

        assert result.columns[1].detected_type == ColumnType.INTEGER
        assert result.sample_rows[1]["value"] is None


class TestDCATParser:
    """Tests for DCAT parser."""

    def test_parse_json_ld_basic(self, tmp_path: Path) -> None:
        """Test parsing basic JSON-LD DCAT metadata."""
        dcat_content = json.dumps({
            "@type": "dcat:Dataset",
            "title": "Test Dataset",
            "description": "A test dataset for unit testing",
            "publisher": {"name": "Test Publisher"},
            "keyword": ["test", "data", "sample"],
        })
        dcat_file = tmp_path / "basic.json"
        dcat_file.write_text(dcat_content)

        result = parse_dcat(str(dcat_file))

        assert isinstance(result, DCATMetadata)
        assert result.title == "Test Dataset"
        assert result.description == "A test dataset for unit testing"
        assert result.publisher == "Test Publisher"
        assert result.keywords == ["test", "data", "sample"]

    def test_parse_json_ld_with_dct_prefix(self, tmp_path: Path) -> None:
        """Test parsing JSON-LD with dct: prefixed properties."""
        dcat_content = json.dumps({
            "@type": "dcat:Dataset",
            "dct:title": "Prefixed Title",
            "dct:description": "Prefixed Description",
            "dct:identifier": "dataset-001",
        })
        dcat_file = tmp_path / "prefixed.json"
        dcat_file.write_text(dcat_content)

        result = parse_dcat(str(dcat_file))

        assert result.title == "Prefixed Title"
        assert result.description == "Prefixed Description"
        assert result.identifier == "dataset-001"

    def test_parse_json_ld_with_graph(self, tmp_path: Path) -> None:
        """Test parsing JSON-LD with @graph structure."""
        dcat_content = json.dumps({
            "@context": {"dcat": "http://www.w3.org/ns/dcat#"},
            "@graph": [
                {
                    "@type": "dcat:Dataset",
                    "title": "Graph Dataset",
                    "description": "Dataset in a graph",
                }
            ],
        })
        dcat_file = tmp_path / "graph.json"
        dcat_file.write_text(dcat_content)

        result = parse_dcat(str(dcat_file))

        assert result.title == "Graph Dataset"

    def test_parse_temporal_coverage(self, tmp_path: Path) -> None:
        """Test parsing temporal coverage."""
        dcat_content = json.dumps({
            "@type": "dcat:Dataset",
            "title": "Temporal Dataset",
            "temporal": {
                "startDate": "2020-01-01",
                "endDate": "2023-12-31",
            },
        })
        dcat_file = tmp_path / "temporal.json"
        dcat_file.write_text(dcat_content)

        result = parse_dcat(str(dcat_file))

        assert result.temporal_coverage is not None
        assert result.temporal_coverage.start_date == "2020-01-01"
        assert result.temporal_coverage.end_date == "2023-12-31"

    def test_parse_spatial_coverage(self, tmp_path: Path) -> None:
        """Test parsing spatial coverage."""
        dcat_content = json.dumps({
            "@type": "dcat:Dataset",
            "title": "Spatial Dataset",
            "spatial": {
                "name": "Zurich",
            },
        })
        dcat_file = tmp_path / "spatial.json"
        dcat_file.write_text(dcat_content)

        result = parse_dcat(str(dcat_file))

        assert result.spatial_coverage is not None
        assert result.spatial_coverage.location == "Zurich"

    def test_parse_publisher_as_string(self, tmp_path: Path) -> None:
        """Test parsing publisher as simple string."""
        dcat_content = json.dumps({
            "@type": "dcat:Dataset",
            "title": "Test",
            "publisher": "Simple Publisher Name",
        })
        dcat_file = tmp_path / "publisher_string.json"
        dcat_file.write_text(dcat_content)

        result = parse_dcat(str(dcat_file))

        assert result.publisher == "Simple Publisher Name"

    def test_parse_publisher_with_foaf_name(self, tmp_path: Path) -> None:
        """Test parsing publisher with foaf:name."""
        dcat_content = json.dumps({
            "@type": "dcat:Dataset",
            "title": "Test",
            "publisher": {"foaf:name": "FOAF Publisher"},
        })
        dcat_file = tmp_path / "publisher_foaf.json"
        dcat_file.write_text(dcat_content)

        result = parse_dcat(str(dcat_file))

        assert result.publisher == "FOAF Publisher"

    def test_parse_keywords_as_single_string(self, tmp_path: Path) -> None:
        """Test parsing keywords when provided as single string."""
        dcat_content = json.dumps({
            "@type": "dcat:Dataset",
            "title": "Test",
            "keyword": "single-keyword",
        })
        dcat_file = tmp_path / "keyword_string.json"
        dcat_file.write_text(dcat_content)

        result = parse_dcat(str(dcat_file))

        assert result.keywords == ["single-keyword"]

    def test_parse_issued_and_modified(self, tmp_path: Path) -> None:
        """Test parsing issued and modified dates."""
        dcat_content = json.dumps({
            "@type": "dcat:Dataset",
            "title": "Test",
            "issued": "2020-01-01",
            "modified": "2024-06-15",
        })
        dcat_file = tmp_path / "dates.json"
        dcat_file.write_text(dcat_content)

        result = parse_dcat(str(dcat_file))

        assert result.issued == "2020-01-01"
        assert result.modified == "2024-06-15"

    def test_parse_license(self, tmp_path: Path) -> None:
        """Test parsing license information."""
        dcat_content = json.dumps({
            "@type": "dcat:Dataset",
            "title": "Test",
            "license": "https://creativecommons.org/licenses/by/4.0/",
        })
        dcat_file = tmp_path / "license.json"
        dcat_file.write_text(dcat_content)

        result = parse_dcat(str(dcat_file))

        assert result.license == "https://creativecommons.org/licenses/by/4.0/"

    def test_parse_contact_point(self, tmp_path: Path) -> None:
        """Test parsing contact point."""
        dcat_content = json.dumps({
            "@type": "dcat:Dataset",
            "title": "Test",
            "contactPoint": {"fn": "John Doe"},
        })
        dcat_file = tmp_path / "contact.json"
        dcat_file.write_text(dcat_content)

        result = parse_dcat(str(dcat_file))

        assert result.contact_point == "John Doe"

    def test_parse_turtle_basic(self, tmp_path: Path) -> None:
        """Test parsing basic Turtle DCAT metadata."""
        turtle_content = """
@prefix dcat: <http://www.w3.org/ns/dcat#> .
@prefix dct: <http://purl.org/dc/terms/> .

<https://example.org/dataset/1>
    a dcat:Dataset ;
    dct:title "Turtle Dataset" ;
    dct:description "A dataset in Turtle format" ;
    dcat:keyword "turtle", "test" .
"""
        turtle_file = tmp_path / "basic.ttl"
        turtle_file.write_text(turtle_content)

        result = parse_dcat(str(turtle_file))

        assert result.title == "Turtle Dataset"
        assert result.description == "A dataset in Turtle format"
        assert "turtle" in result.keywords

    def test_file_not_found_error(self) -> None:
        """Test error when file does not exist."""
        with pytest.raises(DCATParseError, match="File not found"):
            parse_dcat("/nonexistent/path/file.json")

    def test_invalid_json_error(self, tmp_path: Path) -> None:
        """Test error when JSON is invalid."""
        json_file = tmp_path / "invalid.json"
        json_file.write_text("{ invalid json }")

        with pytest.raises(DCATParseError, match="Failed to parse"):
            parse_dcat(str(json_file))

    def test_format_hint_json_ld(self, tmp_path: Path) -> None:
        """Test using format_hint for JSON-LD."""
        dcat_content = json.dumps({"title": "Hint Test"})
        dcat_file = tmp_path / "hint.txt"  # No extension
        dcat_file.write_text(dcat_content)

        result = parse_dcat(str(dcat_file), format_hint="json-ld")

        assert result.title == "Hint Test"

    def test_parse_array_of_datasets(self, tmp_path: Path) -> None:
        """Test parsing JSON array containing datasets."""
        dcat_content = json.dumps([
            {"@type": "dcat:Dataset", "title": "First Dataset"},
            {"@type": "dcat:Dataset", "title": "Second Dataset"},
        ])
        dcat_file = tmp_path / "array.json"
        dcat_file.write_text(dcat_content)

        result = parse_dcat(str(dcat_file))

        # Should parse first dataset
        assert result.title == "First Dataset"

    def test_parse_value_with_language_tag(self, tmp_path: Path) -> None:
        """Test parsing values with @value and @language."""
        dcat_content = json.dumps({
            "@type": "dcat:Dataset",
            "title": {"@value": "German Title", "@language": "de"},
            "description": {"@value": "English Description", "@language": "en"},
        })
        dcat_file = tmp_path / "lang.json"
        dcat_file.write_text(dcat_content)

        result = parse_dcat(str(dcat_file))

        assert result.title == "German Title"
        assert result.description == "English Description"

    def test_handle_utf8_in_dcat(self, tmp_path: Path) -> None:
        """Test handling UTF-8 in DCAT content."""
        dcat_content = json.dumps({
            "@type": "dcat:Dataset",
            "title": "Zürich Bevölkerungsstatistik",
            "description": "Daten über die Bevölkerung in Zürich",
            "publisher": {"name": "Stadt Zürich"},
        })
        dcat_file = tmp_path / "utf8.json"
        dcat_file.write_text(dcat_content, encoding="utf-8")

        result = parse_dcat(str(dcat_file))

        assert result.title == "Zürich Bevölkerungsstatistik"
        assert result.publisher == "Stadt Zürich"


class TestParsedInput:
    """Tests for ParsedInput unified data model."""

    def test_parsed_input_with_both(self, tmp_path: Path) -> None:
        """Test ParsedInput combining CSV and DCAT data."""
        # Create CSV
        csv_content = """name,value\ntest,100"""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text(csv_content)

        # Create DCAT
        dcat_content = json.dumps({
            "@type": "dcat:Dataset",
            "title": "Combined Dataset",
        })
        dcat_file = tmp_path / "meta.json"
        dcat_file.write_text(dcat_content)

        csv_data = parse_csv(str(csv_file))
        dcat_meta = parse_dcat(str(dcat_file))

        parsed = ParsedInput(csv_data=csv_data, dcat_metadata=dcat_meta)

        assert parsed.csv_data.column_names() == ["name", "value"]
        assert parsed.dcat_metadata.title == "Combined Dataset"

    def test_parsed_input_without_dcat(self, tmp_path: Path) -> None:
        """Test ParsedInput with only CSV data."""
        csv_content = """col1,col2\na,b"""
        csv_file = tmp_path / "only.csv"
        csv_file.write_text(csv_content)

        csv_data = parse_csv(str(csv_file))
        parsed = ParsedInput(csv_data=csv_data)

        assert parsed.csv_data is not None
        assert parsed.dcat_metadata is None

    def test_summary_method(self, tmp_path: Path) -> None:
        """Test the summary method."""
        csv_content = """name,age\nAlice,30\nBob,25"""
        csv_file = tmp_path / "summary.csv"
        csv_file.write_text(csv_content)

        dcat_content = json.dumps({
            "title": "Summary Test",
            "description": "A test description that is long enough to be truncated",
            "publisher": {"name": "Test Org"},
            "keyword": ["test", "summary"],
        })
        dcat_file = tmp_path / "summary.json"
        dcat_file.write_text(dcat_content)

        csv_data = parse_csv(str(csv_file))
        dcat_meta = parse_dcat(str(dcat_file))
        parsed = ParsedInput(csv_data=csv_data, dcat_metadata=dcat_meta)

        summary = parsed.summary()

        assert "CSV Source:" in summary
        assert "Columns: 2" in summary
        assert "Total Rows: 2" in summary
        assert "name: string" in summary
        assert "age: int" in summary
        assert "Title: Summary Test" in summary
        assert "Publisher: Test Org" in summary
        assert "Keywords: test, summary" in summary


class TestDCATRawContentAndFormat:
    """Tests for raw_content and source_format fields on DCATMetadata."""

    def test_turtle_raw_content_preserved(self, tmp_path: Path) -> None:
        """Test that raw_content is stored for Turtle DCAT files."""
        turtle_content = """@prefix dcat: <http://www.w3.org/ns/dcat#> .
@prefix dct: <http://purl.org/dc/terms/> .

<https://example.org/dataset/1>
    a dcat:Dataset ;
    dct:title "Raw Content Test" .
"""
        turtle_file = tmp_path / "raw.ttl"
        turtle_file.write_text(turtle_content)

        result = parse_dcat(str(turtle_file))

        assert result.raw_content == turtle_content
        assert result.source_format == "turtle"

    def test_json_ld_raw_content_is_pre_context_injection(self, tmp_path: Path) -> None:
        """Test that raw_content is the original JSON-LD before context injection."""
        original = json.dumps({
            "@type": "dcat:Dataset",
            "title": "JSON-LD Raw Test",
        })
        json_file = tmp_path / "raw.json"
        json_file.write_text(original)

        result = parse_dcat(str(json_file))

        assert result.raw_content == original
        assert result.source_format == "json-ld"
        # The original should NOT have the default context
        assert "http://www.w3.org/ns/dcat#" not in result.raw_content

    def test_source_format_xml(self, tmp_path: Path) -> None:
        """Test that source_format is set for XML DCAT files."""
        xml_content = """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:dcat="http://www.w3.org/ns/dcat#"
         xmlns:dct="http://purl.org/dc/terms/">
  <dcat:Dataset rdf:about="https://example.org/dataset/1">
    <dct:title>XML Dataset</dct:title>
  </dcat:Dataset>
</rdf:RDF>"""
        xml_file = tmp_path / "data.rdf"
        xml_file.write_text(xml_content)

        result = parse_dcat(str(xml_file))

        assert result.source_format == "xml"
        assert result.raw_content == xml_content


class TestDCATFormatToExtension:
    """Tests for dcat_format_to_extension utility."""

    def test_turtle(self) -> None:
        assert dcat_format_to_extension("turtle") == ".ttl"

    def test_ttl(self) -> None:
        assert dcat_format_to_extension("ttl") == ".ttl"

    def test_json_ld(self) -> None:
        assert dcat_format_to_extension("json-ld") == ".jsonld"

    def test_xml(self) -> None:
        assert dcat_format_to_extension("xml") == ".rdf"

    def test_rdf_xml(self) -> None:
        assert dcat_format_to_extension("rdf-xml") == ".rdf"

    def test_unknown_defaults_to_ttl(self) -> None:
        assert dcat_format_to_extension("unknown-format") == ".ttl"
