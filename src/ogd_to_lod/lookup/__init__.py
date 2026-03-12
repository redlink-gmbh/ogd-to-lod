"""SPARQL-based vocabulary reuse lookup."""

from .reuse_context import MatchedDefinedTermSet, MatchedProperty, ReuseContext
from .sparql_client import SPARQLLookup, SPARQLLookupError

__all__ = [
    "SPARQLLookup",
    "SPARQLLookupError",
    "ReuseContext",
    "MatchedProperty",
    "MatchedDefinedTermSet",
]
