"""AI prompts for RML generation."""

RML_GENERATION_PROMPT = """\
Generate a valid RML (RDF Mapping Language) mapping in Turtle format \
based on the approved mapping proposal and CSV schema.

## Prefix Definitions

Always use the following standard prefixes:
- @prefix rr: <http://www.w3.org/ns/r2rml#> .
- @prefix rml: <http://semweb.mmlab.be/ns/rml#> .
- @prefix ql: <http://semweb.mmlab.be/ns/ql#> .
- @prefix csvw: <http://www.w3.org/ns/csvw#> .
- @prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
- @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
- @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
- @prefix schema: <http://schema.org/> .
- @prefix cube: <https://cube.link/> .
- @prefix ex: <{base_uri}> .

Define additional sub-prefixes for each sub-path of the base URI that you need:
- @prefix ex-property: <{base_uri}property/> .  (for all dimension and measure properties)
- @prefix ex-code: <{base_uri}code/> .  (for keyDimension instances/values)
- @prefix ex-obs: <{base_uri}observation/> .  (for observations)

## CRITICAL: Turtle Syntax Rules

### No `/` in prefixed name local parts
The `/` character is NOT allowed in the local part of a prefixed name. \
This is a hard constraint of the W3C Turtle grammar (PN_LOCAL production).

WRONG — produces invalid Turtle:
  ex:dimension/time
  ex:measure/O3
  ex:observation/{{year}}

CORRECT — use sub-prefixes instead:
  ex-dim:time
  ex-measure:O3
  ex-obs:{{year}}

You MUST define a sub-prefix for every sub-path and use it consistently. \
Never write a prefixed name that contains `/` in the local part.

### No relative IRIs (e.g. <#Name>)
Do NOT use relative IRIs such as `<#LogicalSource>` or `<#TriplesMap>`. \
RMLMapper uses RDF4J which rejects relative IRIs without a @base directive. \
Instead, use prefixed names like `ex:LogicalSource` or `ex:TriplesMap`.

## CSV Source Configuration
The CSV source should use the placeholder: {csv_path}

IMPORTANT: Always use the placeholder `{csv_path}` for the CSV file path. \
This placeholder will be replaced with the actual CSV file path at deployment time, \
making the RML mapping portable and reusable.

## CSV Delimiter
The detected CSV delimiter is: {csv_delimiter}

CRITICAL: Both `rml:source` and `rml:referenceFormulation` MUST be placed \
**inside** the `rml:logicalSource` blank node. Never place them at the TriplesMap level.

If the delimiter is a comma (`,`), use the simple form:
```
rml:logicalSource [
    rml:source "{csv_path}";
    rml:referenceFormulation ql:CSV
];
```

If the delimiter is NOT a comma (e.g. `;` or `\\t`), you MUST nest a CSVW Table \
blank node **inside** `rml:source` so that RMLMapper knows how to parse the file:
```
rml:logicalSource [
    rml:source [
        a csvw:Table;
        csvw:url "{csv_path}";
        csvw:dialect [ a csvw:Dialect; csvw:delimiter "{csv_delimiter}" ]
    ];
    rml:referenceFormulation ql:CSV
];
```

Only include the csvw prefix declaration (`@prefix csvw: ...`) when the delimiter \
is not a comma.

Remember: Always use `{csv_path}` exactly as shown - this is a placeholder that will be \
replaced with the actual file path during deployment.

## Approved Mapping Proposal
{mapping_proposal}

## CSV Schema
{csv_schema}

## RDF Data Cube Structure (CRITICAL)

Each CSV row represents ONE cube:Observation. Each CSV column is either:
- A **key dimension** (dimension property with resource values), OR
- A **measure** (measure property with literal values)

### URI Conventions (MUST FOLLOW)

**Properties** (dimensions and measures):
- All properties use the prefix: `ex-property:` ({base_uri}property/)
- **Time dimensions**: ALWAYS use `ex-property:ZEIT` (never use other names like "year", "date", "time")
- **Spatial dimensions**: ALWAYS use `ex-property:RAUM` (never use other names like "region", "location", "place")
- Other dimensions/measures: `ex-property:{{name}}` (e.g., `ex-property:category`, `ex-property:population`)

**Code values** (keyDimension instances):
- All keyDimension values use the prefix: `ex-code:` ({base_uri}code/)
- Construct from CSV column values: `ex-code:{{column_value}}`
- Example: If CSV has region code "ZH", dimension value is `ex-code:ZH`

### Key Dimensions vs Measures
- **Key dimensions** identify what the observation is about (e.g., region, time period, category)
  - Property: Use `ex-property:` prefix (e.g., `ex-property:RAUM`, `ex-property:category`)
  - Values MUST be resources (URIs), not literals
  - Use `rr:termType rr:IRI` in the object map
  - Construct URIs using `ex-code:` prefix: `ex-code:{{column_value}}`
  - Example: `ex-property:RAUM` → `ex-code:ZH` (resource)

- **Measures** are the actual data values being observed (e.g., population count, temperature)
  - Property: Use `ex-property:` prefix (e.g., `ex-property:population`)
  - Values are literals with appropriate datatypes
  - Use `rr:datatype xsd:decimal`, `xsd:integer`, `xsd:date`, etc.
  - Use `rr:termType rr:Literal` (default)
  - Example: `ex-property:population` → `"12345"^^xsd:integer` (literal)

## RML Structure Requirements

1. **Logical Source**: Define the CSV source as shown in the "CSV Delimiter" \
section above. Both `rml:source` and `rml:referenceFormulation ql:CSV` must be \
properties of the `rml:logicalSource` blank node.

2. **TriplesMap for Observations**: Create ONE main TriplesMap where:
   - Each CSV row becomes one cube:Observation
   - Subject URI combines key dimension values for uniqueness
   - Example template: `ex-obs:{{year}}-{{region}}-{{category}}`
   - Type: `rr:class cube:Observation`

3. **Key Dimension Mappings**: For each key dimension column:
   - Property: Use `ex-property:` prefix
     - Time dimensions → `ex-property:ZEIT`
     - Spatial dimensions → `ex-property:RAUM`
     - Other dimensions → `ex-property:{{dimension_name}}`
   - Object MUST be a resource (URI) using `ex-code:` prefix
   - Use `rr:template` to build URIs from CSV column values
   - ALWAYS include `rr:termType rr:IRI` in the object map
   - Example (spatial dimension):
     ```
     rr:predicateObjectMap [
         rr:predicate ex-property:RAUM;
         rr:objectMap [
             rr:template "{{{{base_uri}}}}code/{{{{region_column}}}}";
             rr:termType rr:IRI
         ]
     ];
     ```
   - Example (time dimension):
     ```
     rr:predicateObjectMap [
         rr:predicate ex-property:ZEIT;
         rr:objectMap [
             rr:template "{{{{base_uri}}}}code/{{{{year_column}}}}";
             rr:termType rr:IRI
         ]
     ];
     ```

4. **Measure Mappings**: For each measure column:
   - Property: Use `ex-property:` prefix (e.g., `ex-property:population`)
   - Object is a literal with appropriate datatype
   - Use `rr:datatype` for numeric or temporal values
   - Example:
     ```
     rr:predicateObjectMap [
         rr:predicate ex-property:population;
         rr:objectMap [
             rml:reference "population_column";
             rr:datatype xsd:integer
         ]
     ];
     ```

5. **Datatype Selection**:
   - Integers: `xsd:integer`
   - Decimals/floats: `xsd:decimal`
   - Dates: `xsd:date` (YYYY-MM-DD)
   - Years: `xsd:gYear` (YYYY)
   - Date-times: `xsd:dateTime`
   - Text: `xsd:string` (or omit for plain literals)

6. **Generate Code Value Resources (REQUIRED)**: For each key dimension, create \
a separate TriplesMap to generate the code value resources (dimension instances):
   - Subject: Use `ex-code:{{column_value}}` template
   - Type: `rr:class schema:DefinedTerm`
   - Add labels: Use `schema:name` or `rdfs:label` for human-readable labels
   - Example TriplesMap for region codes:
     ```
     ex:RegionCodeMap a rr:TriplesMap;
         rml:logicalSource [ ... same as observation map ... ];
         rr:subjectMap [
             rr:template "{{{{base_uri}}}}code/{{{{region_column}}}}";
             rr:class schema:DefinedTerm
         ];
         rr:predicateObjectMap [
             rr:predicate schema:name;
             rr:objectMap [ rml:reference "region_column" ]
         ].
     ```
   - This ensures all code values are properly typed as schema:DefinedTerm.

## Output Format
Provide ONLY the RML Turtle code in a fenced code block with language 'turtle'.
The RML must be syntactically valid Turtle.
Do not include explanations outside the code block.

```turtle
# Your RML mapping here
```
"""

RML_CORRECTION_PROMPT = """\
The RML Turtle you generated has a syntax error. Please fix it.

## Error
{error_message}

## Instructions
- Fix ONLY the issue described above.
- Return the complete corrected RML in a fenced ```turtle``` code block.
- Do NOT change anything else about the mapping.
"""

RML_VALIDATION_PROMPT = """\
Validate the following RML Turtle mapping for syntactic correctness and completeness.

Check for:
1. All required prefixes are defined
2. Turtle syntax is valid
3. All TriplesMaps have:
   - A logical source
   - A subject map
   - At least one predicate-object map
4. All predicate-object maps have valid predicates and object maps
5. Datatype declarations are appropriate for the data

RML to validate:
```turtle
{rml_content}
```

If valid, respond with: VALID
If invalid, respond with: INVALID: <reason>
"""
