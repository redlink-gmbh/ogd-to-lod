"""Transform ogd-to-lod YARRRML into Huwise-native mapping dialect.

Huwise (TPF / RDF exports) expects:
  - Only a ``mappings:`` block (no ``sources:``, no ``{CSV_SOURCE}``, no ``prefixes:``)
  - ``subject:`` / ``predicateobjects:`` (RMLio ``s`` / ``po`` are optional shortcuts but
    we emit Huwise-style keys for compatibility)
  - Full IRIs for predicates, classes, and datatypes (not ``ex-property:`` / ``xsd:``)
  - Inter-resource links as mapping *keys* (not ``~iri`` strings)
  - ``$(field_name)`` uses Huwise technical field ``name`` values
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any

import yaml

_FIELD_REF_RE = re.compile(r"\$\(([^)]+)\)")

_XSD_IRIS = {
    "dateTime": "http://www.w3.org/2001/XMLSchema#dateTime",
    "decimal": "http://www.w3.org/2001/XMLSchema#decimal",
    "string": "http://www.w3.org/2001/XMLSchema#string",
    "gYear": "http://www.w3.org/2001/XMLSchema#gYear",
    "integer": "http://www.w3.org/2001/XMLSchema#integer",
    "double": "http://www.w3.org/2001/XMLSchema#double",
    "boolean": "http://www.w3.org/2001/XMLSchema#boolean",
}


def _fetch_field_map(domain: str, dataset_id: str, headers: dict[str, str]) -> dict[str, str]:
    url = f"https://{domain}/api/explore/v2.1/catalog/datasets/{dataset_id}"
    req = urllib.request.Request(
        url, headers={**headers, "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req ,timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    mapping: dict[str, str] = {}
    for field in payload.get("fields") or []:
        if not isinstance(field, dict):
            continue
        name = field.get("name")
        if not name:
            continue
        mapping[name] = name
        label = field.get("label")
        if label and label != name:
            mapping[label] = name
    return mapping


def _rewrite_field_references(text: str, field_map: dict[str, str]) -> tuple[str, list[str]]:
    unknown: list[str] = []

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        target = field_map.get(key)
        if target is None:
            unknown.append(key)
            return match.group(0)
        return f"$({target})"

    return _FIELD_REF_RE.sub(replace, text), unknown


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value


def _expand_curie(value: str, prefixes: dict[str, str]) -> str:
    if value == "a":
        return "a"
    cleaned = _strip_quotes(value)
    if cleaned.endswith("~iri"):
        cleaned = cleaned[:-4]
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return cleaned
    if ":" not in cleaned:
        return cleaned
    prefix, local = cleaned.split(":", 1)
    base = prefixes.get(prefix)
    if base:
        return f"{base.rstrip('/')}/{local.lstrip('/')}" if local else base.rstrip("/")
    return cleaned


def _expand_template(value: str, prefixes: dict[str, str]) -> str:
    text = _strip_quotes(value)
    if "$(" in text:
        head, tail = text.split("$(", 1)
        head = head.rstrip("/")
        if ":" in head:
            prefix_key, local = head.split(":", 1)
            base = prefixes.get(prefix_key)
            if base:
                base = base.rstrip("/")
                local = local.lstrip("/")
                head_expanded = f"{base}/{local}" if local else base
            else:
                head_expanded = _expand_curie(head, prefixes)
        else:
            head_expanded = _expand_curie(head, prefixes) if head else head
        if not head_expanded:
            return f"$({tail}"
        if tail.startswith("/"):
            return f"{head_expanded}$({tail}"
        return f"{head_expanded}/$({tail}"
    return _expand_curie(text, prefixes)


def _resolve_link_object(
        obj: Any,
        prefixes: dict[str, str],
        mapping_keys: dict[str, dict[str, Any]],
) -> Any:
    if not isinstance(obj, str):
        return obj
    raw = _strip_quotes(obj)
    if raw.endswith("~iri"):
        raw = raw[:-4]
    expanded = _expand_template(raw, prefixes)
    for key, entry in mapping_keys.items():
        subject = entry.get("s") or entry.get("subject") or ""
        subject_str = _strip_quotes(str(subject))
        subject_expanded = _expand_template(subject_str, prefixes)
        if "$(" in subject_expanded and "$(" in expanded:
            if subject_expanded.split("$(")[0] == expanded.split("$(")[0]:
                return key
        elif subject_expanded == expanded:
            return key
    return expanded


def _convert_predicate_object(
        row: list[Any],
        prefixes: dict[str, str],
        mapping_keys: dict[str, dict[str, Any]],
) -> list[Any]:
    if not row:
        return row
    out: list[Any] = list(row)
    if isinstance(out[0], str):
        pred = _expand_curie(out[0], prefixes)
        out[0] = "a" if pred.endswith("#type") or pred == "http://www.w3.org/1999/02/22-rdf-syntax-ns#type" else pred
        if out[0] != "a" and not str(out[0]).startswith(("http://", "https://")):
            out[0] = _expand_curie(str(out[0]), prefixes)
    if len(out) > 1:
        if isinstance(out[1], str):
            raw_obj = _strip_quotes(str(out[1]))
            if raw_obj.startswith("$("):
                out[1] = raw_obj
            else:
                out[1] = _resolve_link_object(out[1], prefixes, mapping_keys)
                if isinstance(out[1], str) and not out[1].startswith(
                        ("http://", "https://", "$(")
                ):
                    out[1] = _expand_curie(str(out[1]), prefixes)
        elif isinstance(out[1], list):
            pass
    if len(out) > 2 and isinstance(out[2], str):
        dtype = _strip_quotes(out[2])
        if dtype.startswith("xsd:"):
            out[2] = _XSD_IRIS.get(dtype[4:], dtype)
        elif dtype.endswith("~lang"):
            out[2] = dtype
        elif not dtype.startswith("http"):
            out[2] = _XSD_IRIS.get(dtype, dtype)
    return out


def _convert_to_huwise_dialect(doc: dict[str, Any]) -> dict[str, Any]:
    prefixes: dict[str, str] = dict(doc.get("prefixes") or {})
    raw_mappings: dict[str, Any] = dict(doc.get("mappings") or {})
    huwise_mappings: dict[str, Any] = {}

    for key, entry in raw_mappings.items():
        if not isinstance(entry, dict):
            continue
        huwise_mappings[key] = dict(entry)

    for key, entry in huwise_mappings.items():
        entry.pop("sources", None)
        if "s" in entry:
            entry["subject"] = _expand_template(str(entry.pop("s")), prefixes)
        elif "subject" in entry:
            entry["subject"] = _expand_template(str(entry["subject"]), prefixes)

        po = entry.pop("po", None) or entry.pop("predicateobjects", None)
        if po is not None:
            converted: list[Any] = []
            for row in po:
                if isinstance(row, list):
                    converted.append(
                        _convert_predicate_object(row, prefixes, huwise_mappings)
                    )
                else:
                    converted.append(row)
            entry["predicateobjects"] = converted

    return {"mappings": huwise_mappings}


def prepare_mapping(
        yarrrml: str,
        *,
        domain: str,
        dataset_id: str | None,
        field_map: dict[str, str] | None = None,
        headers: dict[str, str]
) -> tuple[str, list[str]]:
    if field_map is None and dataset_id:
        field_map = _fetch_field_map(domain, dataset_id, headers)
    text, unknown = _rewrite_field_references(yarrrml, field_map or {})

    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise ValueError("YARRRML root must be a mapping object")

    doc.pop("sources", None)
    huwise_doc = _convert_to_huwise_dialect(doc)
    output = yaml.dump(
        huwise_doc,
        Dumper=_HuwiseDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return output, sorted(set(unknown))


class _HuwiseDumper(yaml.SafeDumper):
    """Dump predicate-object rows as inline [a, b, c] lists."""


def _represent_str(dumper: yaml.Dumper, data: str) -> Any:
    # Bare $(field) in flow-style lists is parsed as a YAML alias.
    if "$(" in data or data.startswith("/"):
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="'")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


def _represent_list(dumper: yaml.Dumper, data: list[Any]) -> Any:
    if data and all(isinstance(item, (str, int, float, bool)) or item is None for item in data):
        return dumper.represent_sequence(
            "tag:yaml.org,2002:seq", data, flow_style=True
        )
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data)


_HuwiseDumper.add_representer(str, _represent_str)
_HuwiseDumper.add_representer(list, _represent_list)