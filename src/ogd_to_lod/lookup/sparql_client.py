"""SPARQL-based lookup for existing cube.link properties and DefinedTerms."""

from ogd_to_lod.logging import get_logger
from .reuse_context import ColumnReuse, MatchedProperty, ReuseContext
from .term_matcher import TermMatcher
from ogd_to_lod.config import SPARQLConfig

logger = get_logger(__name__)

#ToDo: change comments
class SPARQLLookup:
    """Queries a SPARQL endpoint for existing cube.link properties and DefinedTerms.

    Both query types are scoped to resources already present in cube.link-based
    data cubes (cube:Observation subjects), so unrelated RDF data in the same
    endpoint is ignored.
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
        sparql_config = SPARQLConfig()
        term_match = TermMatcher(sparql_config, self._endpoint)

        # try:
        #     context.properties = self._lookup_properties(csv_schema, mapping_proposal)
        # except Exception as e:
        #     logger.warning("Property SPARQL lookup failed: %s", e)

        try:
            context.defined_term_sets = term_match.match_terms(csv_schema, mapping_proposal)
        except Exception as e:
            logger.warning("DefinedTermSet SPARQL lookup failed: %s", e)

        return context