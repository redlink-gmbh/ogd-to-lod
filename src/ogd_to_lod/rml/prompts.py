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

Always include a `delimiter` field, even for comma-separated files. \
Use the placeholder `{csv_path}` for the file path exactly as shown — \
it will be replaced with the actual CSV path at deployment time.

```yaml
sources:
  csvSource:
    access: "{csv_path}"
    referenceFormulation: csv
    delimiter: "{csv_delimiter}"
```

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
`mappings:` key:

```yaml
mappings:
  observations:
    sources:
      - [csvSource~source]
    s: ex-obs:$(YearCol)_$(RegionCol)
    po:
      - [a, cube:Observation]
      - [ex-property:ZEIT, $(YearCol), xsd:gYear]
      - [ex-property:RAUM, ex-code:$(RegionCol)~iri]
  regionCodes:
    sources:
      - [csvSource~source]
    s: ex-code:$(RegionCol)
    po:
      - [a, schema:DefinedTerm]
      - [schema:name, $(RegionCol)]
```

## Approved Mapping Proposal
{mapping_proposal}

## CSV Schema
{csv_schema}

## RDF Data Cube Conventions (CRITICAL)

Each CSV row represents ONE cube:Observation. Each column is either:
- A **key dimension** (dimension property with resource values)
- A **measure** (measure property with literal values)

### URI Conventions (MUST FOLLOW)

**Properties** (dimensions and measures) — all use `ex-property:` prefix:
- Time dimensions: ALWAYS use `ex-property:ZEIT`
- Spatial dimensions: ALWAYS use `ex-property:RAUM`
- Other dimensions/measures: `ex-property:` + the column name

**Code values** (key dimension instances) — all use `ex-code:` prefix:
- Construct from CSV column values: `ex-code:$(columnValue)`

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
