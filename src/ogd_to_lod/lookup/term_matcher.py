from .models import ColumnReuse
from ogd_to_lod.logging import get_logger
from ogd_to_lod.config import SPARQLConfig
from collections import Counter, defaultdict
from ogd_to_lod.lookup.csv_values import CSVValuesError, get_column_values
from ogd_to_lod.lookup.queries import sample_terms_query, term_set_query, sparql_query

logger = get_logger(__name__)

class TermMatcher:

    def __init__(self, config: SPARQLConfig, endpoint: str) -> None:
        self.config = config
        self.endpoint = endpoint

    def _get_categorical_columns(
            self,
            csv_schema: dict,
            mapping_proposal: dict | None,
    ) -> set[str]:
        """Return the set of column names that are categorical dimensions.
        """
        if mapping_proposal:
            return {
                d["column"]
                for d in mapping_proposal.get("dimensions", [])
                    if d.get("type") == "categorical" #does it need to be categorical?
            }
        else:
            return []


    def _candidate_term_sets(self, column: str, sample: list[str]) -> list[str]:
        """
        Returns the URIs of term sets covering `>= sample_threshold` of the
        sample, ordered by coverage descending, capped
        at `max_candidate_term_sets`.
        """

        terms = sparql_query(self.endpoint, sample_terms_query(sample))
        if not terms:
            return []

        covered_by_set: dict[str, set[str]] = defaultdict(set)
        ignored = 0
        for term in terms:
            term_set, name = term.get("termSet"), term.get("name")
            if not term_set:
                # matched a term with no schema:isPartOf
                ignored += 1
                continue
            if name:
                covered_by_set[term_set].add(name)
        if ignored:
            logger.debug(
                "Column '%s': ignored %d matched term(s) without a term set",
                column, ignored,
            )

        sample_size = len(sample)
        ranked = sorted(
            covered_by_set.items(),
            key=lambda kv: (-len(kv[1]), kv[0]),
        )
        return [
            term_set
            for term_set, covered in ranked
            if len(covered) / sample_size >= self.config.sample_threshold
        ][: self.config.max_candidate_term_sets]

    @staticmethod
    def _sample_values(counter: Counter[str], sample_size: int) -> list[str]:
        """Pick up to `sample_size` distinct values from a column.
        """
        if not counter:
            return []
        ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        return [value for value, _count in ranked[:sample_size]]

    def _verify_term_set(
            self,
            column: str,
            term_set_uri: str,
            counter: Counter[str],
            total_rows: int,
            truncated: bool,
    ) -> ColumnReuse:

        rows = sparql_query(self.endpoint, term_set_query(term_set_uri))


        name_to_term: dict[str, str] = {}
        for row in rows:
            name, term = row.get("name"), row.get("term")
            if not name or not term:
                continue
            name_to_term[name] = term

        value_to_term: dict[str, str] = {}
        unmatched_values: list[str] = []
        matched_rows = 0

        for value, count in counter.items():
            term = name_to_term.get(value)
            if term is not None:
                value_to_term[value] = term
                matched_rows += count
            else:
                unmatched_values.append(value)

        distinct_total = len(counter)
        distinct_matched = len(value_to_term)

        return ColumnReuse(
            column=column,
            term_set_uri=term_set_uri,
            coverage=(matched_rows / total_rows) if total_rows else 0.0,
            distinct_coverage=(distinct_matched / distinct_total) if distinct_total else 0.0,
            uri_template=None,
            value_to_term=value_to_term,
            unmatched_values=sorted(unmatched_values),
            normalized_matches=0,
            truncated=truncated,
        )

    def match_terms(
            self,
            csv_schema: dict,
            mapping_proposal: dict | None,
    ) -> list[ColumnReuse]:

        categorical_cols = self._get_categorical_columns(csv_schema, mapping_proposal)
        if not categorical_cols:
            return []

        results: list[ColumnReuse] = []

        # get all values for the categorical columns
        try:
            csv_values = get_column_values(
                source=csv_schema["source"],
                columns=sorted(categorical_cols),
                encoding=csv_schema.get("encoding"),
                delimiter=csv_schema.get("delimiter"),
            )
        except CSVValuesError as e:
            logger.warning("Skipping term matching, could not read CSV values from '%s': %s",
                           csv_schema.get("source"), e)
            return []

        # foreach column pick Sparql.config.sample_size different values
        samples: dict[str, list[str]] = {
            column: self._sample_values(counter, SPARQLConfig.sample_size)
            for column, counter in csv_values.columns.items()
        }

        for column, sample in samples.items():
            if not sample:
                logger.debug("Column '%s' has no values to sample; skipping", column)
                continue
            logger.debug("Column '%s' sample (%d values): %s", column, len(sample), sample)

            #get all terms and find out in wich those defined termset the terms lie
            candidate_termset_uris = self._candidate_term_sets(column, sample)
            if not candidate_termset_uris:
                continue

            counter = csv_values.columns[column]

            evaluated = [
                self._verify_term_set(
                    column, uri, counter, csv_values.total_rows,
                    truncated=column in csv_values.truncated,
                )
                for uri in candidate_termset_uris
            ]

            qualifying = [r for r in evaluated if r.coverage >= self.config.min_row_coverage]
            if qualifying:
                results.append(max(qualifying, key=lambda r: r.coverage))

        return results

