"""SPARQL query builders for DefinedTerm reuse lookup.
"""

from __future__ import annotations

_SCHEMA_PREFIX = "PREFIX schema: <http://schema.org/>"
_CUBE_PREFIX = "PREFIX cube: <https://cube.link/>"
_RDFS_PREFIX = "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>"
_RDF_PREFIX = "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>"

class UnsafeSparqlValueError(ValueError):
    """Raised by `escape_literal`/`safe_iri` guards when building a query
    would otherwise be unsafe."""


def escape_literal(value: str) -> str:
    """Escape a string for safe use inside a SPARQL string literal.
    """
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def safe_iri(uri: str) -> str:
    """Validate a URI for safe interpolation inside `<...>` in SPARQL.

    Rejects empty strings, `<`, `>`, and any whitespace--.

    Raises:
        UnsafeSparqlValueError: if the URI is empty or contains a
            forbidden character.
    """
    if not uri:
        raise UnsafeSparqlValueError("IRI must not be empty")
    if "<" in uri or ">" in uri or any(ch.isspace() for ch in uri):
        raise UnsafeSparqlValueError(f"unsafe IRI: {uri!r}")
    return uri


def sample_terms_query(values: list[str]) -> str:

    if not values:
        raise UnsafeSparqlValueError("sample_terms_query requires at least one value")

    literals = ", ".join(f'"{escape_literal(v)}"' for v in values)
    return f"""{_SCHEMA_PREFIX}
    SELECT ?term ?name ?termSet WHERE {{
      ?term a schema:DefinedTerm ;
            schema:name ?name .
      OPTIONAL {{ ?term schema:isPartOf ?termSet }}
      FILTER(STR(?name) IN ({literals}))
    }}"""


def term_set_query(term_set_uri: str) -> str:
    """term` map for a single `schema:DefinedTermSet`.
    """
    uri = safe_iri(term_set_uri)
    return f"""{_SCHEMA_PREFIX}
    SELECT ?term ?name WHERE {{
      ?term a schema:DefinedTerm ;
            schema:isPartOf <{term_set_uri}> ;
            schema:name ?name .
    }}"""


def properties_for_terms_query(term_uris: list[str], limit: int = 50) -> str:

    if not term_uris:
        raise UnsafeSparqlValueError("properties_for_terms_query requires at least one term URI")

    terms = " ".join(f"<{safe_iri(u)}>" for u in term_uris)
    return f"""{_CUBE_PREFIX}
    {_RDFS_PREFIX}
    {_SCHEMA_PREFIX}
    SELECT ?property ?label (COUNT(DISTINCT ?obs) AS ?usageCount) WHERE {{
      VALUES ?term {{ {terms} }}
      ?obs a cube:Observation ;
           ?property ?term .
      OPTIONAL {{ ?property rdfs:label|schema:name ?label }}
    }}
    GROUP BY ?property ?label
    ORDER BY DESC(?usageCount)
    LIMIT {int(limit)}"""


def observation_properties_query(limit: int = 2000) -> str:
    return f"""{_CUBE_PREFIX}
    {_RDFS_PREFIX}
    {_SCHEMA_PREFIX}
    {_RDF_PREFIX}
    SELECT DISTINCT ?property ?label WHERE {{
      ?obs a cube:Observation ;
           ?property ?value .
      OPTIONAL {{ ?property rdfs:label|schema:name ?label }}
      FILTER(?property != rdf:type)
    }}
    LIMIT {int(limit)}"""

class SPARQLLookupError(Exception):
    """Error during SPARQL lookup."""

    pass

def sparql_query(endpoint: str, query: str) -> list[dict[str, str]]:
    """Execute a SPARQL SELECT query and return rows as dicts.

    Args:
        query: SPARQL SELECT query string.

    Returns:
        List of result rows, each a dict mapping variable name → value string.

    Raises:
        SPARQLLookupError: If the query fails.
    """
    try:
        from SPARQLWrapper import JSON, SPARQLWrapper

        sparql = SPARQLWrapper(endpoint)
        sparql.setQuery(query)
        sparql.setReturnFormat(JSON)
        results = sparql.query().convert()

        rows = []
        for binding in results.get("results", {}).get("bindings", []):
            row = {k: v.get("value", "") for k, v in binding.items()}
            rows.append(row)
        return rows

    except ImportError as e:
        raise SPARQLLookupError(
            "SPARQLWrapper is not installed. "
            "Install it with: pip install SPARQLWrapper"
        ) from e
    except Exception as e:
        raise SPARQLLookupError(f"SPARQL query failed: {e}") from e
