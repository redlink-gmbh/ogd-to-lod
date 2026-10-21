"""Shared IRI scheme for generated resources.

Single source of truth for the IRIs that the metadata generator, the
SHACL shapes generator and the YARRRML post-processing must agree on.
Mirrors the URI scopes used by the RML prompt (see
``rml/generator.py``): cube, observation-set, shape and term sets are
dataset-scoped (slug); properties and codes live under the bare base URI.
"""

from __future__ import annotations

from dataclasses import dataclass

from ogd_to_lod._slug import slugify


@dataclass(frozen=True)
class DatasetIRIs:
    """IRIs derived from ``base_uri`` + optional dataset slug."""

    base: str  # base URI, always with trailing slash
    dataset: str  # dataset namespace, always with trailing slash
    cube: str
    observation_set: str
    shape: str
    property_prefix: str

    def term_set(self, property_local_name: str) -> str:
        """IRI of the schema:DefinedTermSet for a newly minted code column."""
        return f"{self.dataset}termset/{property_local_name}"


def dataset_iris(base_uri: str, output_folder: str | None = None) -> DatasetIRIs:
    """Derive all dataset IRIs.

    Without ``output_folder`` the cube IRI is the bare ``base_uri`` (legacy
    behaviour of the metadata generator).
    """
    base = base_uri if base_uri.endswith("/") else base_uri + "/"
    slug = slugify(output_folder) if output_folder else ""
    if slug:
        cube = base + slug
        dataset = cube + "/"
    else:
        cube = base_uri
        dataset = base
    return DatasetIRIs(
        base=base,
        dataset=dataset,
        cube=cube,
        observation_set=dataset + "observation-set",
        shape=dataset + "shape",
        property_prefix=base + "property/",
    )
