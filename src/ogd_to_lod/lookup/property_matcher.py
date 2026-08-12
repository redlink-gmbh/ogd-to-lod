from collections import defaultdict
from typing import Any
from urllib.parse import urlsplit

import yaml

from ogd_to_lod.ai.service import AIService
from ogd_to_lod.lookup import ColumnReuse, MatchedProperty
from ogd_to_lod.lookup.queries import (
    properties_for_terms_query,
    sparql_query,
)

class PropertyMatcher:

    def match_properties(
            self,
            reused_columns: list[ColumnReuse],
            ai_service: AIService,
            endpoint: str,
            dataset_context: dict[str, Any] | None = None,
    ) -> list[MatchedProperty]:

        #get all unique matched terms
        candidates_by_column: dict[str, list[dict[str, str]]] = {}

        for column in reused_columns:
            unique_terms = list(set(column.value_to_term.values()))
            if not unique_terms:
                continue
            rows = sparql_query(endpoint, properties_for_terms_query(unique_terms))
            if rows:
                candidates_by_column[column.column] = rows

        # one batched LLM call across all columns
        prompt = self._build_prompt(candidates_by_column, dataset_context)
        reply = ai_service.ask_once(prompt)
        confirmed = self._parse_matches(reply)

        columns_by_name = {c.column: c for c in reused_columns}
        matched_properties: list[MatchedProperty] = []

        for column_name, candidates in candidates_by_column.items():
            confirmed_uris = confirmed.get(column_name, set())
            confirmed_candidates = [c for c in candidates if c["property"] in confirmed_uris]
            if not confirmed_candidates:
                continue  # no LLM confirmation -> no property

            best = max(confirmed_candidates, key=lambda c: int(c.get("usageCount", 0)))

            matched = MatchedProperty(
                existing_uri=best["property"],
                label=best["label"],
                matched_column=column_name,
                usage_count=int(best.get("usageCount", 0)),
                source="term",
            )
            columns_by_name[column_name].property = matched
            matched_properties.append(matched)

        return matched_properties

    def _build_prompt(
            self,
            candidates_by_column: dict[str, list[dict[str, str]]],
            dataset_context: dict[str, Any] | None = None,
    ) -> str:

        descriptions = self._format_column_descriptions(dataset_context)

        lines = [
            "For each column, decide which (if any) candidate property matches its meaning.",
            "",
            "Column descriptions:",
            descriptions,
            "",
        ]
        for column_name, candidates in candidates_by_column.items():
            lines.append(f"Column: {column_name}")
            lines.append("Candidates:")
            for c in candidates:
                label = c["label"]
                lines.append(f"  - property: {c['property']}, label: {label}")
            lines.append("")

        lines.append(
            "Respond only with a fenced YAML block:\n"
            "matches:\n"
            "  - column: <name>\n"
            "    property: <uri>\n"
            "    match: yes|no"
        )
        return "\n".join(lines)

    def _parse_matches(self, reply: str) -> dict[str, set[str]]:
        blocks = AIService.parse_response(reply).get_yaml_blocks()
        confirmed: dict[str, set[str]] = defaultdict(set)
        for block in blocks:
            parsed = yaml.safe_load(block)
            for entry in parsed.get("matches", []):
                if entry.get("match") is True:
                    confirmed[entry["column"]].add(entry["property"])

        return confirmed

    def _format_column_descriptions(self, dataset_context: dict[str, Any] | None) -> str:
        """Format column descriptions from dataset context for the AI prompt.

        Args:
            dataset_context: Serialized DatasetContext dict or None.

        Returns:
            Formatted string, or a note that no descriptions are available.
        """
        if not dataset_context:
            return "(no column descriptions provided)"

        column_contexts = dataset_context.get("column_contexts") or {}
        if not column_contexts:
            return "(no column descriptions provided)"

        lines = []
        for col_name, ctx in column_contexts.items():
            desc = ctx.get("description") or ""
            comment = ctx.get("comment") or ""
            line = f"- {col_name}"
            if desc:
                line += f": {desc}"
            if comment:
                line += f" ({comment})"
            lines.append(line)

        return "\n".join(lines)


