"""Tests for RML post-processing (CSV source → {{FILE_URL}} placeholder)."""

import pytest

from ogd_to_lod.rml.postprocess import replace_csv_source_with_placeholder

# -- Fixtures ----------------------------------------------------------------

PREFIXES = """\
@prefix rr: <http://www.w3.org/ns/r2rml#> .
@prefix rml: <http://semweb.mmlab.be/ns/rml#> .
@prefix ql: <http://semweb.mmlab.be/ns/ql#> .
@prefix ex: <https://example.org/> .
"""

COMMA_RML = (
    PREFIXES
    + """
ex:TriplesMap a rr:TriplesMap;
    rml:logicalSource [
        rml:source "data.csv";
        rml:referenceFormulation ql:CSV
    ];
    rr:subjectMap [ rr:template "https://example.org/{id}" ].
"""
)

NON_COMMA_RML = (
    "@prefix rr: <http://www.w3.org/ns/r2rml#> .\n"
    "@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\n"
    "@prefix ql: <http://semweb.mmlab.be/ns/ql#> .\n"
    "@prefix csvw: <http://www.w3.org/ns/csvw#> .\n"
    "@prefix ex: <https://example.org/> .\n"
    "\n"
    "ex:TriplesMap a rr:TriplesMap;\n"
    '    rml:logicalSource [\n'
    '        rml:source [\n'
    '            a csvw:Table;\n'
    '            csvw:url "data.csv";\n'
    '            csvw:dialect [ a csvw:Dialect; csvw:delimiter ";" ]\n'
    '        ];\n'
    '        rml:referenceFormulation ql:CSV\n'
    '    ];\n'
    '    rr:subjectMap [ rr:template "https://example.org/{id}" ].\n'
)


# -- Tests -------------------------------------------------------------------


class TestCommaCSV:
    """rml:source replacement for comma-delimited CSV."""

    def test_replaces_rml_source(self):
        result = replace_csv_source_with_placeholder(COMMA_RML, "data.csv")
        assert 'rml:source "{{FILE_URL}}"' in result
        assert 'rml:source "data.csv"' not in result

    def test_inserts_comment_after_last_prefix(self):
        result = replace_csv_source_with_placeholder(COMMA_RML, "data.csv")
        assert "# Original CSV source: data.csv" in result
        # Comment should come after the last @prefix line
        prefix_pos = result.rfind("@prefix")
        comment_pos = result.find("# Original CSV source:")
        assert comment_pos > prefix_pos


class TestNonCommaCSV:
    """csvw:url replacement for non-comma-delimited CSV."""

    def test_replaces_csvw_url(self):
        result = replace_csv_source_with_placeholder(NON_COMMA_RML, "data.csv")
        assert 'csvw:url "{{FILE_URL}}"' in result
        assert 'csvw:url "data.csv"' not in result

    def test_inserts_comment_after_last_prefix(self):
        result = replace_csv_source_with_placeholder(NON_COMMA_RML, "data.csv")
        assert "# Original CSV source: data.csv" in result


class TestCommentPlacement:
    """Comment is placed after the last @prefix line."""

    def test_comment_before_triples_map(self):
        result = replace_csv_source_with_placeholder(COMMA_RML, "data.csv")
        lines = result.splitlines()
        comment_idx = next(
            i for i, l in enumerate(lines) if l.startswith("# Original CSV source:")
        )
        # The line before the comment should be a @prefix or blank
        # The comment should be between prefixes and the body
        prefix_indices = [i for i, l in enumerate(lines) if l.startswith("@prefix")]
        assert comment_idx > max(prefix_indices)

    def test_no_prefix_prepends_comment(self):
        """When there are no @prefix lines, comment is prepended."""
        rml = 'ex:TriplesMap rml:logicalSource [ rml:source "data.csv" ].'
        result = replace_csv_source_with_placeholder(rml, "data.csv")
        assert result.startswith("# Original CSV source: data.csv")


class TestNoMatch:
    """No replacement when filename is not found."""

    def test_returns_unchanged(self):
        result = replace_csv_source_with_placeholder(COMMA_RML, "other.csv")
        assert result == COMMA_RML

    def test_does_not_insert_comment(self):
        result = replace_csv_source_with_placeholder(COMMA_RML, "other.csv")
        assert "# Original CSV source:" not in result


class TestMultipleTriplesMaps:
    """Multiple TriplesMaps referencing the same CSV."""

    def test_replaces_all_occurrences(self):
        rml = (
            PREFIXES
            + '\nex:Map1 rml:logicalSource [ rml:source "data.csv" ].\n'
            + 'ex:Map2 rml:logicalSource [ rml:source "data.csv" ].\n'
        )
        result = replace_csv_source_with_placeholder(rml, "data.csv")
        assert result.count("{{FILE_URL}}") == 2
        assert 'rml:source "data.csv"' not in result


class TestRegexSpecialChars:
    """Filenames containing regex-special characters."""

    def test_parentheses_in_filename(self):
        rml = PREFIXES + '\nex:Map rml:logicalSource [ rml:source "data(2024).csv" ].\n'
        result = replace_csv_source_with_placeholder(rml, "data(2024).csv")
        assert 'rml:source "{{FILE_URL}}"' in result

    def test_dots_in_filename(self):
        rml = PREFIXES + '\nex:Map rml:logicalSource [ rml:source "my.data.file.csv" ].\n'
        result = replace_csv_source_with_placeholder(rml, "my.data.file.csv")
        assert 'rml:source "{{FILE_URL}}"' in result

    def test_plus_in_filename(self):
        rml = PREFIXES + '\nex:Map rml:logicalSource [ rml:source "data+extra.csv" ].\n'
        result = replace_csv_source_with_placeholder(rml, "data+extra.csv")
        assert 'rml:source "{{FILE_URL}}"' in result
