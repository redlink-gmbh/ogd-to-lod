"""DCAT metadata parser supporting JSON-LD and Turtle formats.

Uses rdflib for proper RDF parsing.
"""

import json
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from rdflib import Graph, Namespace
from rdflib.namespace import DCTERMS, FOAF, RDF
from rdflib.term import Literal, Node, URIRef

from .models import DCATMetadata, SpatialCoverage, TemporalCoverage


class DCATParseError(Exception):
    """Exception raised when DCAT parsing fails."""

    pass


# Namespace definitions
DCAT = Namespace("http://www.w3.org/ns/dcat#")
SCHEMA = Namespace("http://schema.org/")
VCARD = Namespace("http://www.w3.org/2006/vcard/ns#")

# Default JSON-LD context for DCAT
DEFAULT_JSONLD_CONTEXT = {
    "dcat": "http://www.w3.org/ns/dcat#",
    "dct": "http://purl.org/dc/terms/",
    "dcterms": "http://purl.org/dc/terms/",
    "dc": "http://purl.org/dc/terms/",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "schema": "http://schema.org/",
    "vcard": "http://www.w3.org/2006/vcard/ns#",
    "title": "http://purl.org/dc/terms/title",
    "description": "http://purl.org/dc/terms/description",
    "publisher": "http://purl.org/dc/terms/publisher",
    "identifier": "http://purl.org/dc/terms/identifier",
    "issued": "http://purl.org/dc/terms/issued",
    "modified": "http://purl.org/dc/terms/modified",
    "language": "http://purl.org/dc/terms/language",
    "license": "http://purl.org/dc/terms/license",
    "accessRights": "http://purl.org/dc/terms/accessRights",
    "temporal": "http://purl.org/dc/terms/temporal",
    "spatial": "http://purl.org/dc/terms/spatial",
    "keyword": "http://www.w3.org/ns/dcat#keyword",
    "contactPoint": "http://www.w3.org/ns/dcat#contactPoint",
    "name": "http://xmlns.com/foaf/0.1/name",
    "fn": "http://www.w3.org/2006/vcard/ns#fn",
    "hasEmail": "http://www.w3.org/2006/vcard/ns#hasEmail",
    "startDate": "http://schema.org/startDate",
    "endDate": "http://schema.org/endDate",
}


def _is_url(source: str) -> bool:
    """Check if the source is a URL."""
    try:
        result = urlparse(source)
        return result.scheme in ("http", "https")
    except Exception:
        return False


def _read_content(source: str) -> tuple[str, str]:
    """Read content from file path or URL.

    Args:
        source: File path or URL.

    Returns:
        Tuple of (content, format_hint based on extension/content-type).

    Raises:
        DCATParseError: If the content cannot be read.
    """
    format_hint = "unknown"

    if _is_url(source):
        try:
            with urlopen(source, timeout=30) as response:
                content = response.read().decode("utf-8")
                content_type = response.headers.get("Content-Type", "")

                if "json" in content_type:
                    format_hint = "json-ld"
                elif "turtle" in content_type or "ttl" in content_type:
                    format_hint = "turtle"
                elif "rdf" in content_type or "xml" in content_type:
                    format_hint = "xml"
        except Exception as e:
            raise DCATParseError(f"Failed to fetch URL '{source}': {e}") from e
    else:
        path = Path(source)
        if not path.exists():
            raise DCATParseError(f"File not found: {source}")

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = path.read_text(encoding="iso-8859-1")
            except Exception as e:
                raise DCATParseError(f"Failed to read file '{source}': {e}") from e
        except Exception as e:
            raise DCATParseError(f"Failed to read file '{source}': {e}") from e

        # Detect format from extension
        suffix = path.suffix.lower()
        if suffix in (".json", ".jsonld"):
            format_hint = "json-ld"
        elif suffix in (".ttl", ".turtle"):
            format_hint = "turtle"
        elif suffix in (".rdf", ".xml"):
            format_hint = "xml"

    return content, format_hint


def _detect_format(content: str, format_hint: str) -> str:
    """Detect RDF format from content if hint is unknown."""
    if format_hint != "unknown":
        return format_hint

    content_stripped = content.strip()
    if content_stripped.startswith("{") or content_stripped.startswith("["):
        return "json-ld"
    elif "@prefix" in content or content_stripped.startswith("<"):
        return "turtle"
    else:
        return "json-ld"  # Default


def _get_literal_value(graph: Graph, subject: Node, predicate: URIRef) -> str | None:
    """Get a literal value from the graph.

    Args:
        graph: The RDF graph.
        subject: The subject node.
        predicate: The predicate to look for.

    Returns:
        String value or None.
    """
    value = graph.value(subject, predicate)
    if value is None:
        return None
    if isinstance(value, Literal):
        return str(value)
    if isinstance(value, URIRef):
        return str(value)
    return str(value)


def _get_all_literal_values(graph: Graph, subject: Node, predicate: URIRef) -> list[str]:
    """Get all literal values for a predicate.

    Args:
        graph: The RDF graph.
        subject: The subject node.
        predicate: The predicate to look for.

    Returns:
        List of string values.
    """
    values = []
    for obj in graph.objects(subject, predicate):
        if isinstance(obj, Literal):
            values.append(str(obj))
        elif isinstance(obj, URIRef):
            values.append(str(obj))
    return values


def _parse_temporal_coverage(graph: Graph, subject: Node) -> TemporalCoverage | None:
    """Parse temporal coverage from the graph.

    Args:
        graph: The RDF graph.
        subject: The dataset subject.

    Returns:
        TemporalCoverage or None.
    """
    # Try dc:temporal or dct:temporal
    temporal = graph.value(subject, DCTERMS.temporal)
    if temporal is None:
        return None

    # If it's a blank node or URI, look for start/end dates
    start_date = None
    end_date = None

    # Try schema:startDate and schema:endDate
    start_date = _get_literal_value(graph, temporal, SCHEMA.startDate)
    end_date = _get_literal_value(graph, temporal, SCHEMA.endDate)

    # Also try dcat:startDate and dcat:endDate
    if not start_date:
        start_date = _get_literal_value(graph, temporal, DCAT.startDate)
    if not end_date:
        end_date = _get_literal_value(graph, temporal, DCAT.endDate)

    # Also try dcterms
    if not start_date:
        start_date = _get_literal_value(graph, temporal, DCTERMS.start)
    if not end_date:
        end_date = _get_literal_value(graph, temporal, DCTERMS.end)

    if start_date or end_date:
        return TemporalCoverage(start_date=start_date, end_date=end_date)

    # If temporal is a literal, try to parse it
    if isinstance(temporal, Literal):
        temporal_str = str(temporal)
        if "/" in temporal_str:
            parts = temporal_str.split("/")
            return TemporalCoverage(
                start_date=parts[0],
                end_date=parts[1] if len(parts) > 1 else None,
            )
        return TemporalCoverage(start_date=temporal_str)

    return None


def _parse_spatial_coverage(graph: Graph, subject: Node) -> SpatialCoverage | None:
    """Parse spatial coverage from the graph.

    Args:
        graph: The RDF graph.
        subject: The dataset subject.

    Returns:
        SpatialCoverage or None.
    """
    spatial = graph.value(subject, DCTERMS.spatial)
    if spatial is None:
        return None

    location = None
    geometry = None
    bbox = None

    # If it's a URI, use as location
    if isinstance(spatial, URIRef):
        location = str(spatial)
    elif isinstance(spatial, Literal):
        location = str(spatial)
    else:
        # Try to get location name
        location = _get_literal_value(graph, spatial, SCHEMA.name)
        if not location:
            location = _get_literal_value(graph, spatial, FOAF.name)
        if not location:
            location = _get_literal_value(graph, spatial, DCTERMS.title)

        # Try to get geometry
        geometry = _get_literal_value(graph, spatial, DCAT.bbox)
        if not geometry:
            # Look for locn:geometry if available
            locn = Namespace("http://www.w3.org/ns/locn#")
            geometry = _get_literal_value(graph, spatial, locn.geometry)

    if location or geometry or bbox:
        return SpatialCoverage(location=location, geometry=geometry, bbox=bbox)

    return None


def _parse_publisher(graph: Graph, subject: Node) -> str | None:
    """Parse publisher from the graph.

    Args:
        graph: The RDF graph.
        subject: The dataset subject.

    Returns:
        Publisher name or None.
    """
    publisher = graph.value(subject, DCTERMS.publisher)
    if publisher is None:
        return None

    # If publisher is a literal, return it directly
    if isinstance(publisher, Literal):
        return str(publisher)

    # If it's a URI or blank node, try to get the name
    name = _get_literal_value(graph, publisher, FOAF.name)
    if name:
        return name

    name = _get_literal_value(graph, publisher, SCHEMA.name)
    if name:
        return name

    name = _get_literal_value(graph, publisher, DCTERMS.title)
    if name:
        return name

    # Return the URI as fallback
    if isinstance(publisher, URIRef):
        return str(publisher)

    return None


def _parse_contact_point(graph: Graph, subject: Node) -> str | None:
    """Parse contact point from the graph.

    Args:
        graph: The RDF graph.
        subject: The dataset subject.

    Returns:
        Contact point string or None.
    """
    contact = graph.value(subject, DCAT.contactPoint)
    if contact is None:
        return None

    # Try vcard:fn (formatted name)
    fn = _get_literal_value(graph, contact, VCARD.fn)
    if fn:
        return fn

    # Try vcard:hasEmail
    email = graph.value(contact, VCARD.hasEmail)
    if email:
        email_str = str(email)
        # Remove mailto: prefix if present
        if email_str.startswith("mailto:"):
            email_str = email_str[7:]
        return email_str

    return None


def _find_dataset(graph: Graph) -> Node | None:
    """Find the main Dataset in the graph.

    Args:
        graph: The RDF graph.

    Returns:
        The dataset subject or None.
    """
    # Look for dcat:Dataset
    for subject in graph.subjects(RDF.type, DCAT.Dataset):
        return subject

    # Fallback: look for any subject with dc:title
    for subject in graph.subjects(DCTERMS.title, None):
        return subject

    return None


def _parse_graph(graph: Graph, source: str) -> DCATMetadata:
    """Parse DCAT metadata from an RDF graph.

    Args:
        graph: The parsed RDF graph.
        source: Original source path/URL.

    Returns:
        Parsed DCATMetadata.

    Raises:
        DCATParseError: If no dataset is found.
    """
    dataset = _find_dataset(graph)
    if dataset is None:
        raise DCATParseError(f"No dcat:Dataset found in '{source}'")

    # Extract basic metadata
    title = _get_literal_value(graph, dataset, DCTERMS.title)
    description = _get_literal_value(graph, dataset, DCTERMS.description)
    identifier = _get_literal_value(graph, dataset, DCTERMS.identifier)
    issued = _get_literal_value(graph, dataset, DCTERMS.issued)
    modified = _get_literal_value(graph, dataset, DCTERMS.modified)
    language = _get_literal_value(graph, dataset, DCTERMS.language)
    license_val = _get_literal_value(graph, dataset, DCTERMS.license)
    access_rights = _get_literal_value(graph, dataset, DCTERMS.accessRights)

    # Get keywords
    keywords = _get_all_literal_values(graph, dataset, DCAT.keyword)

    # Parse complex fields
    publisher = _parse_publisher(graph, dataset)
    temporal_coverage = _parse_temporal_coverage(graph, dataset)
    spatial_coverage = _parse_spatial_coverage(graph, dataset)
    contact_point = _parse_contact_point(graph, dataset)

    return DCATMetadata(
        source=source,
        title=title,
        description=description,
        publisher=publisher,
        keywords=keywords,
        temporal_coverage=temporal_coverage,
        spatial_coverage=spatial_coverage,
        identifier=identifier,
        issued=issued,
        modified=modified,
        language=language,
        license=license_val,
        access_rights=access_rights,
        contact_point=contact_point,
    )


def _ensure_jsonld_context(content: str) -> str:
    """Ensure JSON-LD content has a proper context for DCAT parsing.

    Args:
        content: JSON-LD content string.

    Returns:
        JSON-LD content with context added if needed.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return content

    # If it's not a dict, wrap it
    if isinstance(data, list):
        data = {"@graph": data}

    # Add default context if not present
    if "@context" not in data:
        data["@context"] = DEFAULT_JSONLD_CONTEXT
    elif isinstance(data["@context"], dict):
        # Merge with default context
        merged = DEFAULT_JSONLD_CONTEXT.copy()
        merged.update(data["@context"])
        data["@context"] = merged
    elif isinstance(data["@context"], list):
        # Append default context
        data["@context"].append(DEFAULT_JSONLD_CONTEXT)

    return json.dumps(data)


def parse_dcat(source: str, format_hint: str | None = None) -> DCATMetadata:
    """Parse DCAT metadata from a file or URL.

    Args:
        source: File path or URL to the DCAT metadata.
        format_hint: Optional format hint ('json-ld', 'turtle', 'xml').
            If None, will try to detect.

    Returns:
        DCATMetadata object containing parsed information.

    Raises:
        DCATParseError: If the DCAT metadata cannot be parsed.
    """
    content, detected_format = _read_content(source)

    # Determine format
    fmt = format_hint or _detect_format(content, detected_format)

    # Preserve original content before any context injection
    raw_content = content

    # Map format hints to rdflib format names
    format_map = {
        "json-ld": "json-ld",
        "turtle": "turtle",
        "ttl": "turtle",
        "xml": "xml",
        "rdf-xml": "xml",
    }
    rdflib_format = format_map.get(fmt, "turtle")

    # For JSON-LD, ensure we have a proper context
    if rdflib_format == "json-ld":
        content = _ensure_jsonld_context(content)

    # Parse with rdflib
    graph = Graph()
    try:
        graph.parse(data=content, format=rdflib_format)
    except Exception as e:
        # Try alternative formats if parsing fails
        formats_to_try = ["turtle", "json-ld", "xml"]
        for alt_format in formats_to_try:
            if alt_format == rdflib_format:
                continue
            try:
                graph = Graph()
                alt_content = content
                if alt_format == "json-ld":
                    alt_content = _ensure_jsonld_context(content)
                graph.parse(data=alt_content, format=alt_format)
                break
            except Exception:
                continue
        else:
            raise DCATParseError(
                f"Failed to parse DCAT metadata from '{source}': {e}"
            ) from e

    metadata = _parse_graph(graph, source)
    metadata.raw_content = raw_content
    metadata.source_format = fmt
    return metadata


def dcat_format_to_extension(source_format: str) -> str:
    """Map a DCAT source format name to a file extension.

    Args:
        source_format: Format string such as "turtle", "json-ld", or "xml".

    Returns:
        Appropriate file extension including the leading dot.
    """
    ext_map = {
        "turtle": ".ttl",
        "ttl": ".ttl",
        "json-ld": ".jsonld",
        "xml": ".rdf",
        "rdf-xml": ".rdf",
    }
    return ext_map.get(source_format, ".ttl")
