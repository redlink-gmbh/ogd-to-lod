"""AI prompts for RML generation."""

RML_GENERATION_PROMPT = """\
Generate a valid YARRRML mapping (YAML-based RML) \
based on the approved mapping proposal and CSV schema.

## Overview

YARRRML is a compact YAML notation for RML (RDF Mapping Language). \
It is converted to Turtle RML by yarrrml-parser before execution by RMLMapper.

## Prefix Definitions

Always declare the following in the `prefixes:` block:

```yaml
prefixes:
  rml: "http://semweb.mmlab.be/ns/rml#"
  rr: "http://www.w3.org/ns/r2rml#"
  ql: "http://semweb.mmlab.be/ns/ql#"
  csvw: "http://www.w3.org/ns/csvw#"
  schema: "http://schema.org/"
  cube: "https://cube.link/"
  xsd: "http://www.w3.org/2001/XMLSchema#"
  ex: "{base_uri}"
  ex-obs: "{base_uri}observation/"
  ex-property: "{base_uri}property/"
  ex-code: "{base_uri}code/"
```

## Source Definition

Declare a named source `csvSource` using the placeholder `{{CSV_SOURCE}}` \
for the file path — it will be replaced with the actual path at validation time.

```yaml
sources:
  csvSource:
    access: "{{CSV_SOURCE}}"
    referenceFormulation: csv
```

Reference it in every mapping entry as `- csvSource` (the source name, not an inline shorthand).

## Subject Template

Use `s:` with the `ex-obs:` prefix for observation subjects. \
Combine key dimension column values for uniqueness:

```yaml
s: ex-obs:$(YearCol)_$(RegionCol)
```

## Predicate-Object Shorthand

Use `po:` with array shorthand:

```yaml
po:
  - [a, cube:Observation]
  - [ex-property:ZEIT, $(YearCol), xsd:gYear]
  - [ex-property:RAUM, ex-code:$(RegionCol)~iri]
  - [ex-property:value, $(ValueCol), xsd:decimal]
```

The `~iri` suffix marks the object as an IRI resource (no datatype). \
For typed literals, add the XSD datatype as the third list element.

## Code Value Resources (REQUIRED for each key dimension)

For each key dimension, create a separate mapping entry to generate \
typed dimension value resources. All mapping entries live under the top-level \
`mappings:` key.

```yaml
mappings:
  observations:
    sources:
      - csvSource
    s: ex-obs:$(YearCol)_$(RegionCodeCol)
    po:
      - [a, cube:Observation]
      - [ex-property:ZEIT, $(YearCol), xsd:gYear]
      - [ex-property:RAUM, ex-code:$(RegionCodeCol)~iri]
  regionCodes:
    sources:
      - csvSource
    s: ex-code:$(RegionCodeCol)
    po:
      - [a, schema:DefinedTerm]
      - [schema:name, $(RegionCol)]
```

## ObservationSet Link (REQUIRED)

The static metadata file declares `<{base_uri}observation-set>` as a \
`cube:ObservationSet`. The YARRRML must add a per-row mapping that links \
this set to each generated observation via `cube:observation` (the \
ObservationSet → Observation direction is the canonical cube.link link; \
there is no `cube:dataSet` predicate — do **not** emit one from \
observations). Use the **same subject template** as the `observations` \
mapping for the object position so the IRIs match exactly:

```yaml
  observationSetLink:
    sources:
      - csvSource
    s: ex:observation-set
    po:
      - [cube:observation, ex-obs:$(YearCol)_$(RegionCodeCol)~iri]
```

Notes:
- The subject is a **CURIE**: `ex:` is declared in the `prefixes:` block \
above as `{base_uri}`, so `ex:observation-set` expands to \
`<{base_uri}observation-set>`. CURIE is the **only** working form for a \
constant IRI subject — angle-bracket forms (`s: <iri>` or \
`s: "<iri>"`) are not valid YARRRML and produce URL-encoded broken IRIs \
in the output (see "Constant IRI subjects" rule below).
- The `~iri` suffix on the object is mandatory so RMLMapper emits the \
observation as a resource, not a literal.

## Approved Mapping Proposal
{mapping_proposal}

## CSV Schema
{csv_schema}

## Column Descriptions (from dataset context)
{column_descriptions}
{reuse_context}
## RDF Data Cube Conventions (CRITICAL)

Each CSV row represents ONE cube:Observation. Each column is either:
- A **key dimension** (dimension property with resource values)
- A **measure** (measure property with literal values)

### URI Conventions (MUST FOLLOW)

**Properties** (dimensions and measures):
- If an existing URI is listed for a column in the "Existing Vocabulary" section above, \
use that full URI as a string literal in the predicate position (not a prefixed name).
- Otherwise use `ex-property:` prefix:
  - Time dimensions: ALWAYS use `ex-property:ZEIT`
  - Spatial dimensions: ALWAYS use `ex-property:RAUM`
  - Other dimensions/measures: `ex-property:` + a **sanitized** form of the column name

**Code values** (key dimension instances):
- If an existing URI template is listed for a column in the "Existing Vocabulary" section \
above, use that template (it already includes the `~iri` suffix).
- Otherwise construct from CSV column values using `ex-code:` prefix: `ex-code:$(columnValue)~iri`
- When reusing an existing URI template, do NOT generate a separate mapping entry for \
schema:DefinedTerm resources for that column — the DefinedTerms already exist.

### IRI-Safe Property Names (CRITICAL)

Property URIs must be valid IRIs. Column names often contain characters that are
**not allowed in IRIs** and must be sanitized:
- Replace spaces with `_`
- Replace or remove brackets `[`, `]`, `(`, `)`
- Replace `.` with `_` when used as separator (e.g. `PM2.5` → `PM2_5`)
- Keep alphanumeric characters and `_`, `-`

Examples:
- Column `O3 [ug/m3]` → property `ex-property:O3_ug_m3`
- Column `NO2 [ug/m3]` → property `ex-property:NO2_ug_m3`
- Column `PM2.5 [ug/m3]` → property `ex-property:PM2_5_ug_m3`
- Column `anzahl personen` → property `ex-property:anzahl_personen`

The **column reference** `$(col)` in the mapping still uses the **exact original column name**
from the CSV header (spaces, brackets and all).

### Quoting in YAML Flow Sequences (CRITICAL)

The `po:` shorthand uses YAML flow sequences: `- [predicate, object, datatype]`.
Any element that contains spaces, brackets `[]`, colons `:`, or other special characters
**must be quoted**:

```yaml
# WRONG — breaks YAML parser:
- [ex-property:PM10_ug_m3, $(PM10 [ug/m3]), xsd:decimal]

# CORRECT — quote elements with special characters:
- ["ex-property:PM10_ug_m3", "$(PM10 [ug/m3])", xsd:decimal]
```

When in doubt, quote all three elements of every `po:` shorthand entry.

**CRITICAL — `~iri` suffix and quoting**: The `~iri` suffix must always be \
**inside** the quoted string, never after the closing quote:

```yaml
# WRONG — ~iri outside the quotes breaks YAML:
- ["ex-property:RAUM", "ex-code:$(RegionCol)"~iri]

# CORRECT — ~iri inside the quotes:
- ["ex-property:RAUM", "ex-code:$(RegionCol)~iri"]
```

**CRITICAL — Constant IRIs in object position**: Bare angle-bracket IRIs \
(`<https://…>`) are only valid as the subject (`s:`). In `po:` shorthand \
they are parsed as plain strings and RMLMapper URL-encodes the `<` and \
`>` into the IRI path, producing broken values like \
`<%3Chttps://…%3E>`. Always use a prefixed CURIE + `~iri` (or a full IRI \
string + `~iri`, no angle brackets):

```yaml
# WRONG — angle-bracket IRI in object position:
- [cube:observation, <https://ld.domain.ch/statistics/observation/2024_CH>]

# CORRECT — prefixed CURIE + ~iri (the prefix is declared in prefixes:):
- [cube:observation, "ex-obs:2024_CH~iri"]

# Also correct — full IRI as a string + ~iri (no angle brackets):
- [cube:observation, "https://ld.domain.ch/statistics/observation/2024_CH~iri"]
```

**CRITICAL — Constant IRI subjects MUST be CURIEs**: Angle-bracket IRIs \
(`<https://…>`) on `s:` lines are **not valid YARRRML** — neither the \
bare nor the quoted form. yarrrml-parser treats the whole `<…>` string \
as a plain template and RMLMapper URL-encodes the `<` and `>` into the \
IRI path, producing broken IRIs like `<%3Chttps://…%3E>`. Use a CURIE \
that resolves via a declared prefix instead.

```yaml
# WRONG — bare angle-bracket IRI on an s: line:
  observationSetLink:
    s: <https://ld.domain.ch/statistics/observation-set>

# WRONG — quoted angle-bracket IRI on an s: line:
  observationSetLink:
    s: "<https://ld.domain.ch/statistics/observation-set>"

# CORRECT — CURIE using a declared prefix (ex: is in prefixes:):
  observationSetLink:
    s: ex:observation-set
```

### Key Dimensions vs Measures

**Key dimensions** (region, year, category, etc.):
- Property: use `ex-property:RAUM`, `ex-property:ZEIT`, or `ex-property:` + name
- Values MUST be IRIs — use `ex-code:$(col)~iri`

**Measures** (observed values):
- Property: `ex-property:` + name
- Values are typed literals: e.g., `$(col), xsd:decimal`

### Datatype Selection
- Integers: `xsd:integer`
- Decimals/floats: `xsd:decimal`
- Dates: `xsd:date` (YYYY-MM-DD)
- Years: `xsd:gYear` (YYYY)
- Date-times: `xsd:dateTime`

### Template Literals (Constructing Values from Partial Column Data)

When a column only contains part of a value (e.g., a year `1998`) but the mapping requires
a complete typed literal (e.g., the year-end date `1998-12-31`), use a **template literal**:
combine the column reference with fixed text inside the shorthand array.

```yaml
# Column 'jahr' contains "1998", map to year-end date "1998-12-31"
- [ex-property:ZEIT, "$(jahr)-12-31", xsd:date]
```

A template literal is triggered when the object string mixes `$(col)` references with
literal text. With a datatype in position [2], yarrrml-parser emits `rr:template` +
`rr:termType rr:Literal`, producing a typed literal — not an IRI.

Use this pattern whenever a dimension value requires padding, a fixed suffix/prefix, or
unit embedding (e.g., `"$(year)-01-01"`, `"$(code)-CH"`).

## Output Format
Provide ONLY the YARRRML in a fenced `yaml` code block. \
Do not include any explanation outside the code block.

```yaml
# Your YARRRML mapping here
```
"""

RML_CORRECTION_PROMPT = """\
The YARRRML mapping you generated has an error. Please fix it.

## Error
{error_message}

## Instructions
- Fix ONLY the issue described above.
- Return the complete corrected YARRRML in a fenced ```yaml``` code block.
- Do NOT change anything else about the mapping.

## Common causes of YAML syntax errors in po: entries
If the error mentions "unexpected characters" or a flow sequence, the likely cause is
unquoted special characters inside a `- [pred, obj, type]` shorthand. Fix by quoting
every element that contains spaces, brackets, colons, or dots:
```yaml
# Wrong:
- [ex-property:PM10_ug_m3, $(PM10 [ug/m3]), xsd:decimal]
# Correct:
- ["ex-property:PM10_ug_m3", "$(PM10 [ug/m3])", xsd:decimal]
```
Also ensure property URIs are IRI-safe (replace spaces/brackets with `_`).

If the error mentions a flow sequence near a `~iri` suffix, the cause is `~iri` placed
**outside** a quoted string. The `~iri` suffix must always be **inside** the quotes:
```yaml
# Wrong — ~iri outside the closing quote:
- ["ex-property:RAUM", "ex-code:$(col)"~iri]
# Correct — ~iri inside the quotes:
- ["ex-property:RAUM", "ex-code:$(col)~iri"]
```

If the resulting RDF contains URL-encoded angle brackets in an IRI
(e.g. `<%3Chttps://…%3E>`), the cause is an angle-bracket IRI used as a
**constant IRI** in YARRRML. yarrrml-parser does not recognise the
angle-bracket form in either `s:` or `po:` shorthand positions and
treats the whole `<…>` string as a template, so RMLMapper URL-encodes
the brackets. Use a CURIE (declared in `prefixes:`) instead.

In **object** position, append `~iri` to the CURIE:
```yaml
# Wrong — bare angle-bracket IRI in object position:
- [cube:observation, <https://ld.domain.ch/statistics/observation/2024_CH>]
# Correct — prefixed CURIE + ~iri:
- [cube:observation, "ex-obs:2024_CH~iri"]
```

In **subject** position, use a bare CURIE — no angle brackets, no quotes,
no `~iri` suffix:
```yaml
# Wrong — bare angle-bracket IRI on s: line:
  observationSetLink:
    s: <https://ld.domain.ch/statistics/observation-set>
# Wrong — quoted angle-bracket IRI on s: line:
  observationSetLink:
    s: "<https://ld.domain.ch/statistics/observation-set>"
# Correct — CURIE using a declared prefix:
  observationSetLink:
    s: ex:observation-set
```
"""

RML_VALIDATION_PROMPT = """\
Validate the following YARRRML mapping for syntactic correctness and completeness.

Check for:
1. Valid YAML syntax
2. Required `prefixes:` block is present
3. Required `mappings:` block is present
4. Each mapping has:
   - A `sources:` entry
   - A subject (`s:`)
   - At least one predicate-object (`po:`)
5. Datatype declarations are appropriate for the data

YARRRML to validate:
```yaml
{rml_content}
```

If valid, respond with: VALID
If invalid, respond with: INVALID: <reason>
"""
