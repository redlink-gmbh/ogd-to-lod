"""SPARQL-based lookup for existing cube.link properties and DefinedTerms."""

from ogd_to_lod.logging import get_logger

from .reuse_context import MatchedDefinedTermSet, MatchedProperty, ReuseContext

logger = get_logger(__name__)

# Minimum fraction of CSV sample values that must match a DefinedTermSet for it to be proposed
MIN_COVERAGE = 0.5


class SPARQLLookupError(Exception):
    """Error during SPARQL lookup."""

    pass


class SPARQLLookup:
    """Queries a SPARQL endpoint for existing cube.link properties and DefinedTerms.

    Both query types are scoped to resources already present in cube.link-based
    data cubes (cube:Observation subjects), so unrelated RDF data in the same
    endpoint is ignored.
    """

    # Query 1: All properties used as predicates on cube:Observation subjects.
    # Returns the property URI and an optional rdfs:label.
    _PROPERTY_QUERY = """
PREFIX cube: <https://cube.link/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>

SELECT DISTINCT ?property ?label WHERE {{
  ?obs a cube:Observation .
  ?obs ?property ?value .
  OPTIONAL {{ ?property rdfs:label ?label }}
  FILTER(?property != rdf:type)
}}
"""

    # Query 2: schema:DefinedTerm instances whose schema:name matches one of
    # the supplied VALUES.  Also returns the schema:DefinedTermSet they belong to.
    _DEFINED_TERM_QUERY = """
PREFIX schema: <http://schema.org/>

SELECT ?termSet ?term ?name WHERE {{
  ?termSet a schema:DefinedTermSet .
  ?term a schema:DefinedTerm ;
        schema:isPartOf ?termSet ;
        schema:name ?name .
  VALUES ?name {{ {values} }}
}}
"""

    def __init__(self, endpoint: str):
        """Initialize with a SPARQL endpoint URL.

        Args:
            endpoint: SPARQL endpoint URL.
        """
        self._endpoint = endpoint

    def build_reuse_context(
        self,
        csv_schema: dict,
        mapping_proposal: dict | None = None,
    ) -> ReuseContext:
        """Run both lookups and return a ReuseContext.

        Args:
            csv_schema: Parsed CSV schema with column names, types and sample values.
            mapping_proposal: Optional approved mapping proposal (used to restrict
                              which columns are treated as dimensions/measures).

        Returns:
            ReuseContext with matched properties and DefinedTermSets.
        """
        context = ReuseContext()

        try:
            context.properties = self._lookup_properties(csv_schema, mapping_proposal)
        except Exception as e:
            logger.warning("Property SPARQL lookup failed: %s", e)

        try:
            context.defined_term_sets = self._lookup_defined_term_sets(csv_schema, mapping_proposal)
        except Exception as e:
            logger.warning("DefinedTermSet SPARQL lookup failed: %s", e)

        return context

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _sparql_query(self, query: str) -> list[dict]:
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

            sparql = SPARQLWrapper(self._endpoint)
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

    def _lookup_properties(
        self,
        csv_schema: dict,
        mapping_proposal: dict | None,
    ) -> list[MatchedProperty]:
        """Find existing cube.link properties matching the CSV columns.

        Fetches all cube:Observation predicates from the endpoint, then matches
        them against the CSV column names by comparing rdfs:label (if present)
        or the local name of the URI against the column name (case-insensitive).

        Args:
            csv_schema: CSV schema dict.
            mapping_proposal: Optional mapping proposal.

        Returns:
            List of matched properties.
        """
        logger.debug("Looking up cube.link properties in SPARQL endpoint %s", self._endpoint)
        rows = self._sparql_query(self._PROPERTY_QUERY)

        if not rows:
            logger.debug("No cube:Observation properties found in endpoint")
            return []

        # Build lookup: label/local_name → (uri, label)
        endpoint_props: list[tuple[str, str, str]] = []  # (match_key, uri, label)
        for row in rows:
            uri = row.get("property", "")
            label = row.get("label", "")
            if not uri:
                continue
            # Use label for matching if available, otherwise fall back to local name
            local_name = uri.split("/")[-1].split("#")[-1]
            match_key = label if label else local_name
            endpoint_props.append((match_key.lower(), uri, label or local_name))

        # Determine which columns to match (all if no proposal)
        column_names = [col["name"] for col in csv_schema.get("columns", [])]

        matched: list[MatchedProperty] = []
        for col_name in column_names:
            col_lower = col_name.lower()
            for match_key, uri, label in endpoint_props:
                if match_key == col_lower:
                    logger.info(
                        "Matched column '%s' to existing property <%s>", col_name, uri
                    )
                    matched.append(
                        MatchedProperty(
                            existing_uri=uri,
                            label=label,
                            matched_column=col_name,
                        )
                    )
                    break  # one match per column

        logger.debug("Found %d property matches", len(matched))
        return matched

    def _lookup_defined_term_sets(
        self,
        csv_schema: dict,
        mapping_proposal: dict | None,
    ) -> list[MatchedDefinedTermSet]:
        """Find existing DefinedTermSets that cover the categorical column values.

        For each column whose sample values are looked up, a DefinedTermSet is
        considered a match when:
        - At least MIN_COVERAGE fraction of sample values are found as schema:DefinedTerm
        - The DefinedTerm URIs share a common prefix, and the URI suffix equals the
          CSV value exactly (enabling a simple template like ``{prefix}$(col)~iri``).

        Args:
            csv_schema: CSV schema dict.
            mapping_proposal: Optional mapping proposal.

        Returns:
            List of matched DefinedTermSets.
        """
        # Determine which columns are categorical (skip temporal/numeric)
        categorical_cols = self._get_categorical_columns(csv_schema, mapping_proposal)
        if not categorical_cols:
            return []

        # Collect all unique sample values across categorical columns
        all_values: dict[str, list[str]] = {}  # column → sample values
        for col in csv_schema.get("columns", []):
            if col["name"] in categorical_cols:
                samples = [str(v) for v in col.get("samples", []) if v]
                if samples:
                    all_values[col["name"]] = samples

        if not all_values:
            return []

        # Build VALUES clause for SPARQL
        flat_values = list({v for vals in all_values.values() for v in vals})
        values_clause = " ".join(f'"{v}"' for v in flat_values)

        logger.debug(
            "Looking up DefinedTerms for %d categorical columns (%d unique values)",
            len(all_values),
            len(flat_values),
        )

        rows = self._sparql_query(self._DEFINED_TERM_QUERY.format(values=values_clause))

        if not rows:
            logger.debug("No matching DefinedTerms found")
            return []

        # Group results: termSet → {name → term_uri}
        term_set_data: dict[str, dict[str, str]] = {}
        for row in rows:
            term_set = row.get("termSet", "")
            term_uri = row.get("term", "")
            name = row.get("name", "")
            if term_set and term_uri and name:
                term_set_data.setdefault(term_set, {})[name] = term_uri

        # For each column, find the best-matching DefinedTermSet
        matched: list[MatchedDefinedTermSet] = []
        for col_name, samples in all_values.items():
            best = self._best_match_for_column(col_name, samples, term_set_data)
            if best:
                matched.append(best)

        logger.debug("Found %d DefinedTermSet matches", len(matched))
        return matched

    def _get_categorical_columns(
        self,
        csv_schema: dict,
        mapping_proposal: dict | None,
    ) -> set[str]:
        """Return the set of column names that are categorical dimensions.

        If a mapping proposal is available, only columns declared as categorical
        dimensions are returned. Otherwise falls back to string-typed columns.
        """
        if mapping_proposal:
            return {
                d["column"]
                for d in mapping_proposal.get("dimensions", [])
                if d.get("type") == "categorical"
            }
        # Fallback: treat string-typed columns as potentially categorical
        return {
            col["name"]
            for col in csv_schema.get("columns", [])
            if col.get("type") in ("string", "categorical")
        }

    def _best_match_for_column(
        self,
        col_name: str,
        samples: list[str],
        term_set_data: dict[str, dict[str, str]],
    ) -> MatchedDefinedTermSet | None:
        """Find the DefinedTermSet with the highest coverage for the given column samples.

        Returns None if no DefinedTermSet reaches MIN_COVERAGE or if no consistent
        URI template can be derived (URI suffix does not match the schema:name value).
        """
        best: MatchedDefinedTermSet | None = None
        best_coverage = 0.0

        for term_set_uri, name_to_uri in term_set_data.items():
            matched_values = [v for v in samples if v in name_to_uri]
            coverage = len(matched_values) / len(samples) if samples else 0.0

            if coverage < MIN_COVERAGE:
                continue

            # Check that URI suffixes match the schema:name values (exact suffix match)
            template = self._detect_uri_template(col_name, matched_values, name_to_uri)
            if template is None:
                logger.debug(
                    "DefinedTermSet <%s> has good coverage for '%s' "
                    "but URI suffixes do not match values — skipping",
                    term_set_uri,
                    col_name,
                )
                continue

            if coverage > best_coverage:
                best_coverage = coverage
                best = MatchedDefinedTermSet(
                    term_set_uri=term_set_uri,
                    uri_template=template,
                    matched_column=col_name,
                    coverage=coverage,
                    sample_matches=matched_values,
                )

        return best

    def _detect_uri_template(
        self,
        col_name: str,
        matched_values: list[str],
        name_to_uri: dict[str, str],
    ) -> str | None:
        """Detect a YARRRML URI template from matched DefinedTerm URIs.

        Checks that every matched value ``v`` satisfies:
            uri.endswith(v)

        If so, extracts the common prefix and returns the template
        ``{prefix}$(col_name)~iri``.  Returns None if the pattern does not hold.
        """
        if not matched_values:
            return None

        prefixes: set[str] = set()
        for v in matched_values:
            uri = name_to_uri.get(v, "")
            if not uri.endswith(v):
                return None
            prefix = uri[: -len(v)]
            prefixes.add(prefix)

        if len(prefixes) != 1:
            # Inconsistent prefixes across values
            return None

        prefix = next(iter(prefixes))
        return f"{prefix}$({col_name})~iri"
