"""Static RDF metadata generation for cube:Cube + cube:ObservationSet.

Produces a Turtle file (typically ``metadata.ttl``) committed alongside the
YARRRML mapping. The file describes the dataset as a cube.link Cube and
declares its ObservationSet, enabling Forward-discovery of observations
via ``cube:observationSet`` and ``cube:observation``.

Per-property constraints (``cube:DimensionConstraint`` /
``cube:MeasureConstraint``) and static dimension values are intentionally
out of scope for this first version — see GitHub issue #41.
"""

from __future__ import annotations

from typing import Any

from ogd_to_lod._slug import slugify
from ogd_to_lod.logging import get_logger

logger = get_logger(__name__)


_PREFIXES = (
    "@prefix cube: <https://cube.link/> .\n"
    "@prefix schema: <http://schema.org/> .\n"
    "@prefix dcterms: <http://purl.org/dc/terms/> .\n"
    "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n"
)


class MetadataGenerator:
    """Deterministically build the static metadata Turtle file.

    The generator does not call the AI service — all content is derived from
    the dataset context and the base URI.
    """

    def generate(
        self,
        base_uri: str,
        dataset_context: dict[str, Any] | None = None,
        output_folder: str | None = None,
    ) -> str:
        """Generate the metadata Turtle string.

        Args:
            base_uri: Base URI for the dataset (used to derive cube and
                observation-set IRIs). Trailing slash is preserved.
            dataset_context: Optional serialized DatasetContext dict.
            output_folder: Optional dataset slug, typically the CLI
                ``--output-folder`` value. When provided, it is appended to
                ``base_uri`` so each dataset gets a unique cube IRI:
                ``<base_uri><slug>``. The ObservationSet then lives under
                ``<base_uri><slug>/observation-set``. When omitted, the cube
                IRI is the bare ``base_uri`` (legacy behaviour).

        Returns:
            Turtle document as a string.
        """
        base_with_slash = base_uri if base_uri.endswith("/") else base_uri + "/"
        slug = slugify(output_folder) if output_folder else ""
        if slug:
            cube_iri = base_with_slash + slug
            obs_set_iri = cube_iri + "/observation-set"
        else:
            cube_iri = base_uri
            obs_set_iri = base_with_slash + "observation-set"

        ctx = dataset_context or {}

        cube_lines: list[str] = [f"<{cube_iri}> a cube:Cube"]

        title = _coerce_str(ctx.get("title"))
        if title:
            cube_lines.append(_indent(f"schema:name {_turtle_string(title)}"))

        description = _coerce_str(ctx.get("description"))
        if description:
            cube_lines.append(_indent(f"schema:description {_turtle_string(description)}"))

        publisher = _coerce_str(ctx.get("publisher"))
        if publisher:
            cube_lines.append(_indent(f"schema:publisher {_turtle_string(publisher)}"))

        identifier = _coerce_str(ctx.get("identifier"))
        if identifier:
            cube_lines.append(_indent(f"dcterms:identifier {_turtle_string(identifier)}"))

        issued = _coerce_str(ctx.get("issued"))
        if issued:
            cube_lines.append(_indent(_date_triple("dcterms:issued", issued)))

        modified = _coerce_str(ctx.get("modified"))
        if modified:
            cube_lines.append(_indent(_date_triple("dcterms:modified", modified)))

        license_ = _coerce_str(ctx.get("license"))
        if license_:
            cube_lines.append(_indent(_license_triple(license_)))

        keywords = ctx.get("keywords") or []
        keyword_strs = [_turtle_string(k) for k in keywords if _coerce_str(k)]
        if keyword_strs:
            cube_lines.append(_indent(f"schema:keywords {', '.join(keyword_strs)}"))

        cube_lines.append(_indent(f"cube:observationSet <{obs_set_iri}>"))

        cube_block = " ;\n".join(cube_lines) + " .\n"

        obs_set_block = f"<{obs_set_iri}> a cube:ObservationSet .\n"

        logger.info(
            "Generated metadata.ttl with cube IRI %s and observation-set %s",
            cube_iri,
            obs_set_iri,
        )

        return _PREFIXES + "\n" + cube_block + "\n" + obs_set_block


def _indent(line: str) -> str:
    return "    " + line


def _coerce_str(value: Any) -> str:
    """Return a stripped string, or empty if the value is missing/non-textual."""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return value.strip()


def _turtle_string(value: str) -> str:
    """Encode a string as a Turtle literal, escaping ``\\``, ``"`` and newlines."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    if "\n" in escaped or "\r" in escaped:
        escaped = escaped.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    return f'"{escaped}"'


def _date_triple(predicate: str, value: str) -> str:
    """Emit a date/dateTime triple, falling back to a plain string literal."""
    if _is_date(value):
        return f'{predicate} "{value}"^^xsd:date'
    if _is_datetime(value):
        return f'{predicate} "{value}"^^xsd:dateTime'
    return f"{predicate} {_turtle_string(value)}"


def _is_date(value: str) -> bool:
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        return False
    return value[:4].isdigit() and value[5:7].isdigit() and value[8:10].isdigit()


def _is_datetime(value: str) -> bool:
    return "T" in value and (value.endswith("Z") or "+" in value[10:] or "-" in value[10:])


def _license_triple(value: str) -> str:
    """Emit a license triple — IRI if value looks like a URL, else string."""
    if value.startswith("http://") or value.startswith("https://"):
        return f"dcterms:license <{value}>"
    return f"dcterms:license {_turtle_string(value)}"


def generate_metadata(
    base_uri: str,
    dataset_context: dict[str, Any] | None = None,
    output_folder: str | None = None,
) -> str:
    """Convenience wrapper around :class:`MetadataGenerator`."""
    return MetadataGenerator().generate(base_uri, dataset_context, output_folder)
