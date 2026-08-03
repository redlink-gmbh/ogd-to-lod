# Spec: Reuse of existing DefinedTerms

Implements [issue #78](https://github.com/redlink-gmbh/ogd-to-lod/issues/78); design input in
[`brainstorm-defined-term-reuse.md`](brainstorm-defined-term-reuse.md).

This spec **replaces** the current `src/ogd_to_lod/lookup/` package completely.

---

## 1. Motivation

The existing implementation does not do what the brainstorm describes, and parts of it have never
worked at all. Verified defects in the code being replaced:

| # | Defect | Evidence |
|---|---|---|
| 1 | `_PROPERTY_QUERY` is a **SPARQL syntax error**. It is a format string with doubled `{{`/`}}` braces but is passed to the endpoint **without** `.format()`, so the braces reach the server verbatim. The resulting exception is swallowed by a bare `except Exception`. Property reuse has therefore never produced a match against a live endpoint. | `sparql_client.py:29` (query), `:150` (call), `:83` (swallow) |
| 2 | `VALUES ?name { "Altstetten" }` cannot match `"Altstetten"@de` — plain literals do not match language-tagged ones. The Zürich target graph is language-tagged, so the DefinedTerm lookup returns nothing there. | `sparql_client.py:52` |
| 3 | No literal escaping when building the `VALUES` clause; a value containing `"` or `\` breaks the query. | `sparql_client.py:229` |
| 4 | Coverage is computed over **3 sample values from the first 20 rows**, then reported to the user as a percentage of the dataset. With `MIN_COVERAGE = 0.5` the test degenerates to "at least 2 of 3 values found". | `nodes.py:105-119` (parse/truncate), `sparql_client.py:10`, `:301` |
| 5 | Key dimensions are never used. `lookup_node` runs **before** `propose_node`, so `state.mapping_proposal` is always `None` and the categorical-dimension branch is dead code; production always falls back to "string-typed columns". | `flow.py:92-109`, `sparql_client.py:272-277` |
| 6 | `_detect_uri_template` only handles prefix + identity suffix, and requires it for *every* matched value — any deviation drops the whole column. No LLM involvement, no verification step. | `sparql_client.py:329-359` |
| 7 | The property query is unbounded — every predicate of every observation, no `LIMIT`, no timeout. | `sparql_client.py:29-40` |

**Goal.** A per-column reuse result — term set, exact row-level coverage, verified URI template,
unmatched values, matched property — gated per column by the user and injected into RML generation
and metadata generation.

---

## 2. Design decisions

| # | Decision |
|---|---|
| D1 | The lookup runs **after mapping-proposal approval**, before GENERATE, so real key dimensions are available. |
| D2 | The URI mapping expression is **proposed by the LLM** from the enriched table, then **verified locally**. A column whose template fails verification keeps its property but loses code reuse. |
| D3 | Property discovery is **term-derived first** (brainstorm Q3), with a **name-based fallback** for columns without a term match — notably numeric measures, which never have DefinedTerms. |
| D4 | The confirmation gate is **per column**: accept all, reject all, or exclude named columns. |
| D5 | The verification pass **streams every CSV row** and counts distinct values; coverage is exact, not sampled. |
| D6 | Name matching is **exact in SPARQL** (`FILTER(STR(?name) IN (...))`), then **normalized locally** (NFKC + casefold + collapsed whitespace) against the downloaded full term set. |
| D7 | LLM calls are **batched** (one per stage) and use a new **isolated** call path so reuse Q&A does not bloat the conversation history used by GENERATE. |

### Deliberate deviations from the brainstorm

- **Sampling is deterministic, not random.** The brainstorm says "n zufällige, unterschiedliche
  Werte". This spec takes the **n most frequent distinct values** instead: runs become reproducible,
  tests become stable, and the most frequent values are precisely the ones whose match dominates
  row coverage.
- **The mapping expression is a YARRRML template, not a free-form expression.** The brainstorm's
  example output is `'https://…/code/Q' + $SPALTE_Quartier`. The mapping must be executable by the
  RML pipeline, so the artefact is the equivalent YARRRML template
  `https://…/code/Q$(Quartier)~iri`, matching the conventions already taught in
  `rml/prompts.py:150-158`.

---

## 3. Module layout

`src/ogd_to_lod/lookup/`

| File | Responsibility |
|---|---|
| `models.py` | `MatchedProperty`, `ColumnReuse`, `ReuseContext` |
| `queries.py` | SPARQL query **builder functions** + literal/IRI escaping |
| `sparql_client.py` | `SPARQLClient.select()` transport, `SPARQLLookupError` |
| `csv_values.py` | Full-CSV distinct-value counting |
| `term_matcher.py` | Part 1 — steps 1–8 |
| `template.py` | LLM template proposal + local verification |
| `property_matcher.py` | Part 2 + name-based fallback |
| `builder.py` | `build_reuse_context(...)` orchestration |

**Deleted:** `reuse_context.py` (→ `models.py`). **Rewritten:** `sparql_client.py` (transport only),
`tests/test_lookup.py`.

**Removed symbols:** `MIN_COVERAGE`, `MatchedDefinedTermSet`, `SPARQLLookup.build_reuse_context`,
`_lookup_properties`, `_lookup_defined_term_sets`, `_get_categorical_columns`,
`_best_match_for_column`, `_detect_uri_template`.

---

## 4. Data model — `lookup/models.py`

```python
@dataclass
class MatchedProperty:
    existing_uri: str
    label: str
    matched_column: str          # name retained — metadata/generator.py:132 depends on it
    usage_count: int = 0
    source: str = "term"         # "term" (Q3) or "name" (fallback)


@dataclass
class ColumnReuse:
    column: str
    term_set_uri: str
    coverage: float              # matched rows / total rows — exact
    distinct_coverage: float     # matched distinct values / distinct values
    uri_template: str | None     # verified YARRRML template; None if unrepresentable
    template_verified: bool = False
    value_to_term: dict[str, str] = field(default_factory=dict)
    unmatched_values: list[str] = field(default_factory=list)
    normalized_matches: int = 0  # values that matched only after normalization
    truncated: bool = False      # distinct-value cap hit → coverage is a lower bound
    property: MatchedProperty | None = None


@dataclass
class ReuseContext:
    columns: list[ColumnReuse] = field(default_factory=list)
    properties: list[MatchedProperty] = field(default_factory=list)  # flat, all sources
```

`ReuseContext.properties` deliberately keeps its existing shape — a flat `list[MatchedProperty]`
carrying `matched_column` — so `metadata/generator.py:132-140` requires **no change**.
`ColumnReuse` carries the brainstorm's per-column output structure.

Methods on `ReuseContext`: `has_matches()`, `to_prompt_text()`, `to_display_text()`,
`enriched_table(sample_rows)`, `drop_columns(names)`.

Mapping from the brainstorm's output structure:

| Brainstorm field | This spec |
|---|---|
| `column` | `ColumnReuse.column` |
| `term_set` | `ColumnReuse.term_set_uri` |
| `coverage` | `ColumnReuse.coverage` |
| `mapping` | `ColumnReuse.uri_template` (+ `template_verified`) |
| `value_to_term` | `ColumnReuse.value_to_term` |
| `unmatched_values` | `ColumnReuse.unmatched_values` |
| `property` | `ColumnReuse.property` |

---

## 5. SPARQL layer

### 5.1 `queries.py` — builder functions

Queries are **functions returning finished strings**, not class constants with `.format()`
placeholders. This structurally eliminates the defect class of §1.1 — there is no unapplied
format step to forget.

```python
def escape_literal(value: str) -> str          # escapes \  "  \n  \r  \t
def safe_iri(uri: str) -> str                  # rejects <, >, whitespace; else returns uri
def sample_terms_query(values: list[str]) -> str
def term_set_query(term_set_uri: str) -> str
def properties_for_terms_query(term_uris: list[str], limit: int = 50) -> str
def observation_properties_query(limit: int = 2000) -> str
```

**Q1 — `sample_terms_query`** (brainstorm step 3; one call per column). `FILTER(STR(?name) IN (...))`
rather than `VALUES`, so language-tagged names match. `schema:isPartOf` stays `OPTIONAL`; terms
without a term set cannot win and are logged then ignored.

```sparql
PREFIX schema: <http://schema.org/>
SELECT ?term ?name ?termSet WHERE {
  ?term a schema:DefinedTerm ;
        schema:name ?name .
  OPTIONAL { ?term schema:isPartOf ?termSet }
  FILTER(STR(?name) IN ("Altstetten", "Wipkingen", "Enge"))
}
```

**Q2 — `term_set_query`** (brainstorm step 6; one call per candidate term set).

```sparql
PREFIX schema: <http://schema.org/>
SELECT ?term ?name WHERE {
  ?term a schema:DefinedTerm ;
        schema:isPartOf <TERMSET_URI> ;
        schema:name ?name .
}
```

**Q3 — `properties_for_terms_query`** (brainstorm Part 2 step 1). `usageCount` is the tie-breaker
when several properties carry the same term as value; a missing label falls back to the URI's local
name. Bounded by `LIMIT`.

```sparql
PREFIX cube: <https://cube.link/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX schema: <http://schema.org/>
SELECT ?property ?label (COUNT(DISTINCT ?obs) AS ?usageCount) WHERE {
  VALUES ?term { <term1> <term2> <term3> }
  ?obs a cube:Observation ;
       ?property ?term .
  OPTIONAL { ?property rdfs:label|schema:name ?label }
}
GROUP BY ?property ?label
ORDER BY DESC(?usageCount)
LIMIT 50
```

**Q4 — `observation_properties_query`**: the fixed, **bounded** replacement for the broken
`_PROPERTY_QUERY`, used only for the name-based fallback (§8.2).

### 5.2 `sparql_client.py` — transport only

`SPARQLClient(endpoint: str, timeout: int)` exposing `select(query: str) -> list[dict[str, str]]`.
Configures `setTimeout`, `setMethod(POST)`, `setReturnFormat(JSON)`. `SPARQLWrapper` stays a lazy
import raising `SPARQLLookupError` on `ImportError`, as today. The binding-flattening loop is
carried over from `sparql_client.py:117-121`.

---

## 6. Part 1 — term matching (`term_matcher.py`)

### 6.1 Candidate columns (brainstorm step 1)

Taken from the **approved** `state.mapping_proposal.to_dict()["dimensions"]` — all proposal
dimensions become `cube:KeyDimension` (`metadata/generator.py:259-262`). Excluded:

- `dimension_type == "temporal"`;
- columns whose `csv_schema` detected type is `int`, `float`, `boolean`, `date` or `datetime`.

Measures are never candidates.

### 6.2 Full value pass (D5)

`csv_values.py` reuses `parsers/csv_parser.py::_read_file_content` (URL support + encoding
detection) and `_detect_delimiter`, then streams `csv.DictReader` over the content, building a
`Counter[str]` per candidate column plus the true row count. Distinct values per column are capped
at `max_distinct_values` (default 50 000); on overflow the column is marked `truncated=True`, a
warning is logged, and its coverage is treated as a lower bound.

Returns `(dict[column, Counter[str]], total_rows)`. The true row count also removes the reliance on
the fast line-count estimate in `csv_parser.py:289-293`.

### 6.3 Sample (brainstorm step 2)

`sample_size` (default 8) distinct values per column: the **most frequent** distinct values, ties
broken by sorted order. See the deviation note in §2.

### 6.4 Candidate term sets (brainstorm steps 3–5)

One Q1 per column. Group result rows by `?termSet` and count how many sampled values each covers.
Keep term sets covering `>= sample_threshold` (default 0.5) of the sample, ordered by coverage
descending, at most `max_candidate_term_sets` (default 3). The runners-up are the fallback when the
winner fails the full check in §6.5 — this is what handles the brainstorm's
"different granularity / hierarchy level" case.

### 6.5 Full verification (brainstorm steps 6–7)

One Q2 per candidate term set → the complete `name → term_uri` map. All distinct column values are
matched against it: exact first, then on a normalized key
(`unicodedata.normalize("NFKC", v).casefold()` with whitespace collapsed), incrementing
`normalized_matches`. Then compute:

- `coverage` — sum of row counts of matched values ÷ `total_rows`;
- `distinct_coverage` — matched distinct values ÷ total distinct values;
- `unmatched_values` — values present in the CSV but absent from the term set (the brainstorm's
  "nicht vorkommende Terms"; truncated for display with a residual count).

### 6.6 Acceptance (brainstorm step 8)

Accept the candidate with the highest `coverage` where `coverage >= min_row_coverage` (default 0.9 —
the brainstorm's *x %*). Columns below threshold produce **no** `ColumnReuse`, but their best
coverage is logged so it is visible why nothing was proposed.

**Round-trip budget:** one Q1 per candidate column plus one Q2 per candidate term set. Independent
of CSV size, as the brainstorm requires; the full comparison happens locally in Python.

---

## 7. Template proposal and verification (`template.py`)

### 7.1 Enriched table

Per accepted column, take up to 10 rows from `state.csv_schema["sample_rows"]` (already populated at
`nodes.py:117`) and append a `<column>_uri` column filled from `value_to_term`, writing
`(no match)` where absent — the brainstorm's example table:

| Jahr | Quartier | Quartier_uri | Anzahl |
|---|---|---|---|
| 2023 | Altstetten | https://ld.stadt-zuerich.ch/statistics/code/Q_Altstetten | 1234 |
| 2023 | Enge | *(no match)* | 456 |

### 7.2 LLM call (D2, D7)

**One batched call** covering all accepted columns, answered as a fenced YAML block and parsed with
the existing `AIService.parse_response(...).get_yaml_blocks()` (`ai/service.py:102-108`) — the
established structured-output pattern in this codebase, since there is no JSON-mode support.

```yaml
templates:
  - column: Quartier
    template: "https://ld.stadt-zuerich.ch/statistics/code/Q_$(Quartier)~iri"
```

### 7.3 Local verification

A template is accepted only when **all** hold:

1. it contains exactly one `$(<column>)` placeholder, for its own column;
2. it ends with `~iri`;
3. substituting each matched raw value reproduces that value's term URI for **100 %** of
   `value_to_term`.

On failure: `uri_template = None`, `template_verified = False`. Per D2 the column keeps its property
and RML generates fresh `ex-code:` URIs for it. Percent-encoded or otherwise transformed term URIs
land in this bucket by design; the column and reason are logged.

---

## 8. Part 2 — property matching (`property_matcher.py`)

### 8.1 Term-derived (primary)

Per accepted column, Q3 with up to `max_terms_for_property_query` (default 20) term URIs from
`value_to_term`. Candidate label = `?label`, else the URI local name.

**One batched LLM call** compares each candidate label against the column name *and* its description
from `state.dataset_context` (see `rml/generator.py:322 _format_column_descriptions` for how
descriptions are read), answered as YAML:

```yaml
matches:
  - column: Quartier
    property: https://ld.stadt-zuerich.ch/statistics/property/quartier
    match: yes
```

Select the highest-`usageCount` candidate the LLM confirms. If none is confirmed, accept the top
candidate only when its label is normalized-equal to the column name; otherwise the column gets no
property.

### 8.2 Name-based fallback (D3)

A single Q4 call, matching normalized label-or-local-name against column names, applied **only** to
columns with no term-derived property. This is the old behaviour with the syntax defect fixed and a
`LIMIT` added, and it is what preserves property reuse for numeric measures.

---

## 9. `AIService` — isolated call path (D7)

`src/ogd_to_lod/ai/service.py` gains:

```python
def ask_once(self, message: str) -> str
```

Refactor the retry / token-accounting / callback / request-limit body of `send_message`
(`service.py:396-451` plus its `except` blocks) into a private `_invoke(messages) -> str`. Then:

- **`send_message`** — builds messages **with** history, appends the user `Message` before invoking
  and the assistant `Message` after, preserving the `APIConnectionError` → `history.pop()`
  behaviour at `service.py:476`.
- **`ask_once`** — invokes with `[SystemMessage(self._system_prompt), HumanMessage(message)]` only
  and **never** touches `_conversation_history`. It still counts against `_request_limit` and still
  reports token usage through the existing callbacks.

Rationale: `send_message` unconditionally appends both prompt and reply to `_conversation_history`
(`service.py:409`, `:447-449`), so every reuse question would otherwise inflate the context of all
later GENERATE calls — the same latent problem the intent-detection call at `nodes.py:365` has.

---

## 10. Configuration

`SPARQLConfig` (`src/ogd_to_lod/config.py:36-40`) gains:

```python
endpoint: str | None = None
timeout: int = 30                       # seconds per query
sample_size: int = 8                    # distinct values per column for Q1 (brainstorm step 2)
sample_threshold: float = 0.5           # min sample fraction for a term set to be a candidate
max_candidate_term_sets: int = 3
min_row_coverage: float = 0.9           # brainstorm's x %
max_distinct_values: int = 50_000
max_terms_for_property_query: int = 20
normalize_values: bool = True
```

Also fix the loader fragility at `config.py:195-199` and `:221-227`: replace the `if sparql_data:`
guard and the duplicated `return Config(...)` with `sparql_data = config_data.get("sparql") or {}`
and a single unconditional `SPARQLConfig(...)` construction.

All keys documented (commented out; endpoint still disabled by default) in
`config/config.yaml:24-28`.

---

## 11. Flow integration

### 11.1 `graph/nodes.py`

- `analyze_node:154` transitions to `FlowState.PROPOSE` instead of `FlowState.LOOKUP`.
- `lookup_node(state, config, ai_service)` — rewritten; gains `ai_service`, calls
  `build_reuse_context(...)`, and on matches sets `FlowState.LOOKUP` + `awaiting_user_input`.
  With no endpoint configured, no key dimensions, or no matches, it sets an empty `ReuseContext()`
  and `FlowState.GENERATE` so the caller proceeds directly to generation.

### 11.2 `graph/flow.py`

- `_build_graph:63-64, 101-109` — remove the `lookup` / `wait_for_lookup` nodes and
  `_route_from_lookup`; `_route_from_analyze:303` returns `"propose"`. In its new position the
  compiled graph would never reach LOOKUP anyway (it stops at the first wait node, `wait_for_input`
  after `propose`); `ASK_CSV_URL` is existing precedent for a `FlowState` handled purely in
  `continue_with_input`.
- Extract `continue_with_input:450-473` (generate → tier 1 → tier 2 → confirm_name / refine) into
  `_run_generate_and_validate() -> GraphState`, called from two sites.
- `continue_with_input:447` — when `handle_user_input` yields `FlowState.GENERATE`, call
  `lookup_node` first; if it set `awaiting_user_input`, return and wait; otherwise
  `return self._run_generate_and_validate()`.
- `_handle_lookup_confirmation:535-578` — rewritten for the per-column gate (D4):
  - `yes` / `y` / `ja` / `ok` / `all` → keep everything;
  - `no` / `n` / `nein` / `skip` / `none` → replace with an empty `ReuseContext()`;
  - comma-separated column names or 1-based indices → `ReuseContext.drop_columns(...)`;
  - anything else → re-prompt.

  On resolution it calls `_run_generate_and_validate()`. It must **no longer** call `propose_node`.

### 11.3 `graph/state.py`

`reuse_context: ReuseContext | None` stays (`state.py:129`); update the import. `to_dict():195`
reports the accepted column count alongside the existing boolean.

### 11.4 `cli.py`

Add a branch at `cli.py:243-255` for the already-defined but never-called
`flow.is_awaiting_lookup_confirmation()` (`flow.py:717`):

```
Reuse existing terms? (yes / no / comma-separated columns to exclude):
```

---

## 12. RML and metadata integration

- `rml/generator.py:169-176` — the log line `len(reuse_context.defined_term_sets)` becomes
  `len(reuse_context.columns)`. Everything else is unchanged: the `{reuse_context}` placeholder at
  `rml/prompts.py:136` and the threading through `generate()` / `generate_rml()` stay as-is.
- `ReuseContext.to_prompt_text()` emits, per accepted column: the reused property URI, the
  **verified** template — or an explicit *"term set matched but no representable template; generate
  fresh `ex-code:` URIs for this column"* — the coverage, and the enriched sample table.
- `rml/prompts.py:150-158` — add one bullet to the "Code values" list covering the
  matched-but-no-template case, so the model still uses the reused property there.
- `metadata/generator.py` — **unchanged**, because `ReuseContext.properties` keeps its shape (§4).

---

## 13. Tests

`tests/test_lookup.py` is rewritten, stubbing `SPARQLClient.select` and `AIService.ask_once`.

**`queries.py`**
- `escape_literal` handles `"`, `\`, newlines; `safe_iri` rejects `>` and whitespace.
- Q1 emits `FILTER(STR(?name) IN (...))`, not `VALUES ?name`.
- **Regression:** no builder output contains a literal `{{` or `}}` (defect §1.1).

**`csv_values.py`**
- Exact row count and distinct counts on a fixture CSV; `truncated` set when the distinct cap is
  hit; non-comma delimiter handled.

**`term_matcher.py`**
- Winner selection; fallback to a runner-up when the winner fails `min_row_coverage`;
  language-tagged names matched; normalization counted in `normalized_matches`; exact `coverage`,
  `distinct_coverage` and `unmatched_values`; below-threshold column yields no `ColumnReuse`.

**`template.py`**
- Correct prefix template accepted; wrong prefix, missing `$(col)`, missing `~iri`, and
  partially-reproducing templates each rejected → `template_verified is False` while `property`
  survives.

**`property_matcher.py`**
- `usageCount` ordering; LLM `yes`/`no` YAML parsing; no confirmation and no name equality → no
  property; name-based fallback picks up a numeric measure column.

**`ai/service.py`**
- `ask_once` leaves `conversation_history` unchanged, increments `request_count`, and still fires
  token callbacks.

**Flow**
- `lookup_node` is reached after proposal approval and not after analyze; no endpoint → straight to
  GENERATE with an empty context; per-column exclusion in `_handle_lookup_confirmation` drops
  exactly the named column and then generates.

**Existing tests**
- `tests/test_graph.py:250` (`test_analyze_csv_success`) asserts `FlowState.LOOKUP` after analyze
  and must be updated.
- `tests/test_metadata.py:270-302` and `tests/test_config.py:118-140` should keep passing
  unchanged — confirm rather than rewrite.

---

## 14. Verification

1. `pytest tests/test_lookup.py tests/test_graph.py tests/test_metadata.py tests/test_config.py tests/test_ai.py`
2. Full suite: `pytest`
3. **Live, local.** `docker compose --profile fuseki up -d` (`docker-compose.yml:52-68`, port 3030).
   Load a fixture graph containing a `schema:DefinedTermSet`, its `schema:DefinedTerm`s with
   **language-tagged** `schema:name`s, and a handful of `cube:Observation`s referencing them. Set
   `sparql.endpoint: "http://localhost:3030/test/query"` and run `ogd-to-lod` on a CSV whose
   key-dimension values match those terms. Confirm from the logs and the gate output: candidate term
   sets found, exact row coverage reported, a template proposed **and verified**, and a property
   discovered via Q3.
4. **Live, real target graph.** Point `sparql.endpoint` at `https://ld.stadt-zuerich.ch/query` and
   run a Zürich-quarter CSV — the case the old code could not handle at all (defects §1.1 and §1.2).
   Inspect the generated YARRRML for the reused `.../statistics/code/...` template and the reused
   property URI, and check that no `schema:DefinedTerm` block was emitted for the reused column.
5. **Negative checks.** Endpoint unset → flow proceeds to GENERATE with an empty context and no
   extra prompt. Endpoint unreachable → warning logged, empty context, flow still completes.

---

## 15. Documentation to update

`README.md:96-110`, `architecture.md:22-23`, `ROADMAP.md:13`.