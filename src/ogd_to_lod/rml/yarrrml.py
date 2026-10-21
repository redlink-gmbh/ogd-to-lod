"""Read-only view on a generated YARRRML document.

Normalises the different YARRRML spellings the LLM produces (``s`` /
``subject``, ``po`` / ``predicateobjects``, list vs. dict entries, ``~iri``
vs. ``type: iri``) into small dataclasses, so the term-set post-processing
and the SHACL shapes generator can reason about predicates, objects,
datatypes and the CSV columns behind them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from ogd_to_lod.logging import get_logger

logger = get_logger(__name__)

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

_PLACEHOLDER_RE = re.compile(r"\$\(([^)]+)\)")


def expand_prefix(term: str, prefixes: dict[str, str]) -> str:
    """Expand ``prefix:local`` with the YARRRML prefixes; leave anything else as is."""
    if ":" not in term:
        return term
    prefix, _, local = term.partition(":")
    base = prefixes.get(prefix)
    return f"{base}{local}" if base else term


def local_name(iri: str) -> str:
    """Last path / fragment segment of an IRI."""
    return re.split(r"[/#]", iri.rstrip("/#"))[-1]


@dataclass
class TermRef:
    """A subject or object value: constant or ``$(col)`` template."""

    template: str  # IRIs prefix-expanded; literals verbatim
    is_iri: bool
    datatype: str | None = None  # expanded IRI
    language: str | None = None

    @property
    def columns(self) -> list[str]:
        return _PLACEHOLDER_RE.findall(self.template)

    @property
    def is_constant(self) -> bool:
        return not self.columns

    @property
    def literal_prefix(self) -> str:
        """Fixed text before the first placeholder (the whole value if constant)."""
        return self.template.split("$(", 1)[0]


@dataclass
class PredicateObject:
    predicate: str  # expanded IRI; ``a`` becomes rdf:type
    obj: TermRef
    raw: Any = None  # the original po entry (identity used for edits)


@dataclass
class MappingView:
    name: str
    raw: dict[str, Any]
    subject: TermRef | None
    po: list[PredicateObject] = field(default_factory=list)

    @property
    def po_key(self) -> str:
        return "predicateobjects" if "predicateobjects" in self.raw else "po"

    def types(self) -> set[str]:
        return {p.obj.template for p in self.po if p.predicate == RDF_TYPE}


def load_yarrrml(text: str) -> dict[str, Any]:
    """Parse YARRRML text; raises ``yaml.YAMLError`` / ``ValueError`` on bad input."""
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict) or not isinstance(doc.get("mappings"), dict):
        raise ValueError("YARRRML has no 'mappings' block")
    return doc


def dump_yarrrml(doc: dict[str, Any]) -> str:
    """Serialise a YARRRML dict; leaf lists stay in flow style (``[p, o]``)."""
    return yaml.safe_dump(
        doc, sort_keys=False, allow_unicode=True, default_flow_style=None, width=10_000
    )


def get_prefixes(doc: dict[str, Any]) -> dict[str, str]:
    prefixes = doc.get("prefixes") or {}
    return {str(k): str(v) for k, v in prefixes.items()} if isinstance(prefixes, dict) else {}


def iter_mappings(doc: dict[str, Any]) -> list[MappingView]:
    """Normalised view of every mapping in ``doc['mappings']``."""
    prefixes = get_prefixes(doc)
    views: list[MappingView] = []
    for name, raw in (doc.get("mappings") or {}).items():
        if not isinstance(raw, dict):
            continue
        view = MappingView(name=str(name), raw=raw, subject=_parse_subject(raw, prefixes))
        entries = raw.get("predicateobjects", raw.get("po")) or []
        for entry in entries if isinstance(entries, list) else []:
            parsed = _parse_po(entry, prefixes)
            if not parsed:
                logger.warning("Unsupported po entry in mapping '%s': %r", name, entry)
            view.po.extend(parsed)
        views.append(view)
    return views


def _parse_subject(raw: dict[str, Any], prefixes: dict[str, str]) -> TermRef | None:
    subject = raw.get("s", raw.get("subject", raw.get("subjects")))
    if isinstance(subject, list):
        subject = subject[0] if subject else None
    if isinstance(subject, dict):
        subject = subject.get("value")
    if not isinstance(subject, str):
        return None
    # subjects are always IRIs; strip a stray ~iri
    return TermRef(_strip_iri(expand_prefix(subject, prefixes)), is_iri=True)


def _parse_po(entry: Any, prefixes: dict[str, str]) -> list[PredicateObject]:
    if isinstance(entry, list) and len(entry) >= 2:
        preds, obj = entry[0], entry[1]
        extra = entry[2] if len(entry) > 2 else None
        datatype, language = _datatype_or_language(extra, prefixes)
        opts: dict[str, Any] = {"datatype": datatype, "language": language}
    elif isinstance(entry, dict):
        preds = entry.get("p", entry.get("predicates", entry.get("predicate")))
        obj = entry.get("o", entry.get("objects", entry.get("object")))
        opts = {
            "datatype": _expand_opt(entry.get("datatype"), prefixes),
            "language": entry.get("language"),
        }
    else:
        return []

    pred_list = preds if isinstance(preds, list) else [preds]
    obj_list = obj if isinstance(obj, list) else [obj]
    result: list[PredicateObject] = []
    for p in pred_list:
        if not isinstance(p, str):
            continue
        predicate = RDF_TYPE if p in ("a", "rdf:type") else expand_prefix(p, prefixes)
        for o in obj_list:
            term = _parse_object(o, predicate, prefixes, opts)
            if term is not None:
                result.append(PredicateObject(predicate, term, raw=entry))
    return result


def _parse_object(
    o: Any, predicate: str, prefixes: dict[str, str], opts: dict[str, Any]
) -> TermRef | None:
    datatype, language = opts.get("datatype"), opts.get("language")
    if isinstance(o, dict):
        value = o.get("value")
        if not isinstance(value, str):
            return None
        is_iri = o.get("type") == "iri"
        datatype = _expand_opt(o.get("datatype"), prefixes) or datatype
        language = o.get("language") or language
    elif isinstance(o, (str, int, float, bool)):
        value = str(o)
        is_iri = value.endswith("~iri")
    else:
        return None

    if predicate == RDF_TYPE:
        is_iri = True  # class objects are IRIs without ~iri
    if is_iri:
        return TermRef(_strip_iri(expand_prefix(_strip_iri(value), prefixes)), is_iri=True)
    return TermRef(value, is_iri=False, datatype=datatype, language=language)


def _datatype_or_language(extra: Any, prefixes: dict[str, str]) -> tuple[str | None, str | None]:
    if not isinstance(extra, str):
        return None, None
    if extra.endswith("~lang"):
        return None, extra[: -len("~lang")]
    return expand_prefix(extra, prefixes), None


def _expand_opt(value: Any, prefixes: dict[str, str]) -> str | None:
    return expand_prefix(value, prefixes) if isinstance(value, str) else None


def _strip_iri(value: str) -> str:
    return value[: -len("~iri")] if value.endswith("~iri") else value
