from __future__ import annotations

import base64
import requests
import yaml
from rdflib import RDF, Graph, Namespace, URIRef

from .models import MappingTemplate, Property

REPO = "opendatabs/ogd-to-lod"
API = f"https://api.github.com/repos/{REPO}"

SCHEMA = Namespace("http://schema.org/")
CUBE = Namespace("https://cube.link/")

_ROLE_BY_TYPE = {
    CUBE.KeyDimension: "dimension",
    CUBE.MeasureDimension: "measure",
}


def get_mapping_branches(session, api: str):
    branches = []
    page = 1
    while True:
        resp = session.get(api, params={"per_page": 100, "page": page})
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break

        for b in batch:
            name = b["name"]
            if name.startswith("mapping/"):
                branches.append(name)

                if len(branches) >= 5: #for testing only 2
                    return branches

    return branches


def get_file_content(session, path, ref):
    resp = session.get(f"{API}/contents/{path}", params={"ref": ref})
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    return base64.b64decode(data["content"]).decode("utf-8")


def collect_mappings(api: str):
    """load YARRRML + rdflib-Graph per mapping from repo."""
    session = requests.Session()

    results = {}
    for branch in get_mapping_branches(session, api):
        dsnr = branch.split("/", 1)[1]  # datasetnumber
        base = f"mapping/{dsnr}"
        yarrrml_raw = get_file_content(session, f"{base}/mapping.yarrrml.yaml", branch)
        ttl_raw = get_file_content(session, f"{base}/metadata.ttl", branch)

        mapping = yaml.safe_load(yarrrml_raw) if yarrrml_raw else None

        g = None
        if ttl_raw:
            g = Graph()
            g.parse(data=ttl_raw, format="turtle")

        results[dsnr] = {"mapping": mapping, "metadata_graph": g}

    return results

def _expand_prefix(term: str, prefixes: dict[str, str]) -> str:
    if ":" not in term:
        return term
    prefix, _, local = term.partition(":")
    base = prefixes.get(prefix)
    return f"{base}{local}" if base else term


def extract_properties_from_yarrrml(mapping: dict) -> dict[str, Property]:

    prefixes = mapping.get("prefixes", {})
    property_prefixes = {
        p for p, uri in prefixes.items() if uri.rstrip("/").split("/")[-1] == "property"
    }

    properties: dict[str, Property] = {}
    for block in mapping.get("mappings", {}).values():
        for po in block.get("po", []):
            if not isinstance(po, list) or len(po) < 2:
                continue
            predicate, obj = po[0], po[1]
            if not isinstance(predicate, str) or ":" not in predicate:
                continue
            if predicate.split(":", 1)[0] not in property_prefixes:
                continue

            uri = _expand_prefix(predicate, prefixes)
            datatype = po[2] if len(po) > 2 and isinstance(po[2], str) else None

            properties[uri] = Property(
                property_uri=uri,
                label=None,
                role="attribute",
                datatype=datatype,
            )
    return properties


def enrich_properties_from_ttl(properties: dict[str, Property], graph: Graph) -> None:
    """label (schema:name) und role (cube:KeyDimension/MeasureDimension) in-place ergänzen."""
    for uri, prop in properties.items():
        subject = URIRef(uri)
        name = graph.value(subject, SCHEMA.name)
        if name is not None:
            prop.label = str(name)
        for rdf_type in graph.objects(subject, RDF.type):
            role = _ROLE_BY_TYPE.get(rdf_type)
            if role:
                prop.role = role
                break


def detect_cube_shape(graph: Graph) -> str:
    measure_count = sum(1 for _ in graph.subjects(RDF.type, CUBE.MeasureDimension))
    return "multi-measure" if measure_count > 1 else "single-measure"


def build_mapping_template(branch: str, dsnr: str, mapping: dict, graph: Graph) -> MappingTemplate:
    properties = extract_properties_from_yarrrml(mapping)
    enrich_properties_from_ttl(properties, graph)
    return MappingTemplate(
        branch=branch,
        dsnr=dsnr,
        properties=list(properties.values()),
        cube_shape=detect_cube_shape(graph),
    )

def collect_mapping_templates(api: str) -> list[MappingTemplate]:
    templates: list[MappingTemplate] = []
    for dsnr, data in collect_mappings(api).items():
        mapping, graph = data.get("mapping"), data.get("metadata_graph")
        if not mapping or graph is None:
            continue
        templates.append(build_mapping_template(
            branch=f"mapping/{dsnr}", dsnr=dsnr, mapping=mapping, graph=graph,
        ))
    return templates