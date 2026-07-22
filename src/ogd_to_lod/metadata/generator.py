"""Static RDF metadata generation for cube:Cube + cube:ObservationSet.

Produces a Turtle file (typically ``metadata.ttl``) committed alongside the
YARRRML mapping. The file describes the dataset as a cube.link Cube,
declares its ObservationSet, and (when a mapping proposal is supplied)
emits per-dimension / per-measure ``schema:name`` and
``schema:description`` triples on the property IRIs so downstream
consumers can read column documentation directly from the RDF.

Richer cube.link constructs (``cube:DimensionConstraint`` /
``cube:MeasureConstraint``, code lists, SHACL paths) remain out of scope —
see GitHub issue #41.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ogd_to_lod._slug import slugify
from ogd_to_lod.logging import get_logger

if TYPE_CHECKING:
    from ogd_to_lod.lookup import ReuseContext

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
        mapping_proposal: dict[str, Any] | None = None,
        reuse_context: ReuseContext | None = None,
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
            mapping_proposal: Optional approved mapping proposal dict
                (``MappingProposal.to_dict()``). When provided, a
                ``schema:name`` + ``schema:description`` block is emitted
                for each dimension and measure property IRI.
            reuse_context: Optional SPARQL-based reuse context. Columns whose
                property is reused from an existing endpoint URI are skipped
                here — that vocabulary is already defined upstream, so we do
                not redefine it under our own ``property/`` IRI.

        Returns:
            Turtle document as a string.
        """
        base_with_slash = base_uri if base_uri.endswith("/") else base_uri + "/"
        slug = slugify(output_folder) if output_folder else ""
        # Cube and ObservationSet are dataset-specific (slug-scoped); properties
        # are shared concepts and live under the bare base URI so they can be
        # reused across datasets (mirrors the YARRRML ex-property: prefix).
        if slug:
            cube_iri = base_with_slash + slug
            obs_set_iri = cube_iri + "/observation-set"
        else:
            cube_iri = base_uri
            obs_set_iri = base_with_slash + "observation-set"
        property_prefix = base_with_slash + "property/"

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

        # Columns whose property is reused from an existing endpoint URI: do
        # not emit a local property block for them (the vocabulary already
        # exists upstream, and the YARRRML predicate is the existing URI, not
        # our property/<name> IRI).
        reused_cols = (
            {p.matched_column for p in reuse_context.properties}
            if reuse_context
            else set()
        )

        property_blocks: list[str] = []
        if mapping_proposal:
            column_contexts = (
                (dataset_context or {}).get("column_contexts") or {}
            )
            descriptors: list[dict[str, Any]] = []
            for dim in mapping_proposal.get("dimensions", []) or []:
                if dim.get("column", "") in reused_cols:
                    continue
                d = _property_descriptor(
                    property_prefix,
                    column=dim.get("column", ""),
                    column_contexts=column_contexts,
                    dimension_type=dim.get("type"),
                    kind="dimension",
                )
                if d:
                    descriptors.append(d)
            for measure in mapping_proposal.get("measures", []) or []:
                if measure.get("column", "") in reused_cols:
                    continue
                d = _property_descriptor(
                    property_prefix,
                    column=measure.get("column", ""),
                    column_contexts=column_contexts,
                    dimension_type=None,
                    kind="measure",
                )
                if d:
                    descriptors.append(d)
            merged = _merge_descriptors(descriptors)
            property_blocks = [_render_property_block(d) for d in merged]

        logger.info(
            "Generated metadata.ttl with cube IRI %s, observation-set %s, "
            "%d property block(s)",
            cube_iri,
            obs_set_iri,
            len(property_blocks),
        )

        out = _PREFIXES + "\n" + cube_block + "\n" + obs_set_block
        if property_blocks:
            out += "\n" + "\n".join(property_blocks)
        return out


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


_PROPERTY_LOCAL_NAME_CLEANUP = re.compile(r"[\s./\[\]\(\)]+")
_PROPERTY_LOCAL_NAME_KEEP = re.compile(r"[^A-Za-z0-9_\-]")
_PROPERTY_LOCAL_NAME_COLLAPSE = re.compile(r"_+")


def _property_local_name(column: str, dimension_type: str | None) -> str:
    """Mirror the YARRRML prompt's property naming convention.

    Time dimensions resolve to ``ZEIT``, spatial dimensions to ``RAUM``;
    everything else uses a sanitised version of the column header where
    whitespace, brackets, dots, and slashes become ``_``, all other
    non-IRI-safe characters are dropped, and runs of ``_`` are collapsed.
    """
    if dimension_type == "temporal":
        return "ZEIT"
    if dimension_type == "spatial":
        return "RAUM"
    s = _PROPERTY_LOCAL_NAME_CLEANUP.sub("_", column or "")
    s = _PROPERTY_LOCAL_NAME_KEEP.sub("", s)
    s = _PROPERTY_LOCAL_NAME_COLLAPSE.sub("_", s).strip("_")
    return s or "property"


_PROPERTY_TYPES = {
    "dimension": "cube:KeyDimension",
    "measure": "cube:MeasureDimension",
}


def _property_descriptor(
    property_prefix: str,
    column: str,
    column_contexts: dict[str, Any],
    dimension_type: str | None,
    kind: str,
) -> dict[str, Any] | None:
    """Compute the property IRI + metadata fields for one proposal entry.

    Returns ``None`` for empty column names. Otherwise a dict with keys
    ``iri``, ``kind``, ``rdf_type``, ``column``, ``description``, ``comment``.
    Rendering and de-duplication happen later (see ``_merge_descriptors``
    and ``_render_property_block``).
    """
    if not column:
        return None

    local = _property_local_name(column, dimension_type)
    iri = property_prefix + local
    ctx = column_contexts.get(column) or {}
    return {
        "iri": iri,
        "kind": kind,
        "rdf_type": _PROPERTY_TYPES.get(kind, "cube:KeyDimension"),
        "column": column,
        "description": _coerce_str(ctx.get("description")),
        "comment": _coerce_str(ctx.get("comment")),
    }


def _merge_descriptors(
    descriptors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group descriptors by IRI; first-wins for ``schema:name`` / ``kind``.

    When multiple proposal entries collide on the same property IRI — for
    instance two temporal dimensions both mapped to ``ZEIT`` — only the
    first is emitted; non-empty ``description`` / ``comment`` from later
    entries back-fill empty fields on the first. A warning is logged
    naming every colliding column so the user can fix the proposal or
    rename the source columns.
    """
    merged: dict[str, dict[str, Any]] = {}
    collisions: dict[str, list[str]] = {}
    for d in descriptors:
        iri = d["iri"]
        if iri not in merged:
            merged[iri] = dict(d)
            continue
        existing = merged[iri]
        collisions.setdefault(iri, [existing["column"]]).append(d["column"])
        for field in ("description", "comment"):
            if not existing[field] and d[field]:
                existing[field] = d[field]
    for iri, columns in collisions.items():
        logger.warning(
            "Multiple proposal entries map to the same property IRI <%s>: "
            "%s. Emitting a single merged block from the first entry — "
            "remap the conflicting columns in the proposal step or rename "
            "them upstream.",
            iri,
            ", ".join(columns),
        )
    return list(merged.values())


def _render_property_block(d: dict[str, Any]) -> str:
    """Render one merged property descriptor as a Turtle block."""
    lines = [f"<{d['iri']}> a {d['rdf_type']}"]
    lines.append(_indent(f"schema:name {_turtle_string(d['column'])}"))
    if d["description"]:
        lines.append(_indent(f"schema:description {_turtle_string(d['description'])}"))
    if d["comment"]:
        lines.append(_indent(f"schema:disambiguatingDescription {_turtle_string(d['comment'])}"))
    return " ;\n".join(lines) + " .\n"


def generate_metadata(
    base_uri: str,
    dataset_context: dict[str, Any] | None = None,
    output_folder: str | None = None,
    mapping_proposal: dict[str, Any] | None = None,
    reuse_context: ReuseContext | None = None,
) -> str:
    """Convenience wrapper around :class:`MetadataGenerator`."""
    return MetadataGenerator().generate(
        base_uri,
        dataset_context,
        output_folder,
        mapping_proposal,
        reuse_context,
    )
