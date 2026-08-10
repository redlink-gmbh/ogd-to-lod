"""SPARQL-based vocabulary reuse lookup — orchestrates term matching,
template proposal/verification, and property matching into a ReuseContext.
"""

from ogd_to_lod.ai.service import AIService
from ogd_to_lod.config import SPARQLConfig
from ogd_to_lod.logging import get_logger
from ogd_to_lod.lookup.models import ReuseContext
from ogd_to_lod.lookup.template import propose_and_verify_templates
from ogd_to_lod.lookup.term_matcher import TermMatcher

logger = get_logger(__name__)

class SPARQLLookup:
    """Queries a SPARQL endpoint for existing cube.link properties and DefinedTerms."""

    def __init__(self, endpoint: str):
        """Initialize with a SPARQL endpoint URL.

        Args:
            endpoint: SPARQL endpoint URL.
        """
        self._endpoint = endpoint

    def build_reuse_context(
            self,
            csv_schema: dict,
            ai_service: AIService,
            mapping_proposal: dict | None = None,
    ) -> ReuseContext:
        """Run term matching, template verification, and property matching.

        Args:
            csv_schema: Parsed CSV schema with column names, types and sample values.
            ai_service: AI service used for a isolated subagent call
                (template proposal, property confirmation).
            mapping_proposal: Approved mapping proposal (used to restrict
                              which columns are treated as dimensions/measures).

        Returns:
            ReuseContext with matched properties and per-column term/template reuse.
        """
        context = ReuseContext()
        sparql_config = SPARQLConfig()
        term_match = TermMatcher(sparql_config, self._endpoint)

        try:
            context.columns = term_match.match_terms(csv_schema, mapping_proposal)
        except Exception as e:
            logger.warning("DefinedTermSet SPARQL lookup failed: %s", e)
            return context

        if context.columns:
            try:
                sample_rows = csv_schema.get("sample_rows", [])
                # Same full row set for every column
                # picks out only the cells for its own column per row.
                sample_rows_by_column = {col.column: sample_rows for col in context.columns}
                propose_and_verify_templates(ai_service, context.columns, sample_rows_by_column)
            except Exception as e:
                logger.warning("Template proposal/verification failed: %s", e)

        # TODO: property matching
        # try:
        #     context.properties = term_match.match_properties(context.columns)
        # except Exception as e:
        #     logger.warning("Property SPARQL lookup failed: %s", e)

        return context