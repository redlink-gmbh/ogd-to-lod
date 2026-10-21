# Project: Open Govermental Data to Linked Open Data

## 1. What I want to achieve
* We want to create a Tool that creates RML (RDF Mapping Language https://rml.io/specs/rml/) for given CSV files and their associated descriptive metadata (DCAT).
* The Tool uses generative AI (GPT Model from Azure) to provide a mapping configuration.
* The Tool creates RML that can transform the given CSV to a data cubes (must use the cube.link vocabulary (https://cube.link/) and schema.org vocabulary (https://schema.org/)
* The Tool generates dimensions and their hierarchies using schema.org/DefinedTerm, schema.org/DefinedTermSet and schema.org/inDefinedTermSet.
* The Tool must have a conversation interface with the user to guide the user through the process.
* The Tool stores the generated RML (one per CSV) and other RDF-files (e.g. for Dimension Instances) in a GitHub repository (folders /mappings, /dimensions). It creates a PR for each new mapping. The PR should include a human-readable description of the mapping and a preview of one or two datapoints (in RDF and human readable form).
* The Tool should also reuse existing dimensions and measurements (from already deployed data cubes), including properties, instances and units if it is appropriate. It uses SPARQL to get information about already deployed data cubes (e.g. dimensions and mesurements).
* The Tool supports also adaption of existing mappings (or dimension hierarchies)) which is stored in a GitHub repository.
* The Tool validates the generated RML before creating the PR (e.g., by running it against a sample of the CSV to catch errors early).
* DCAT metadata is provided by the user together with the CSV file.
* The SPARQL endpoint URL is provided by the user and stored in a memory/config file committed to the GitHub repository.
* The base URI pattern for new resources (e.g., `https://ld.stadt-zuerich.ch/statistics/...`) is provided by the user at the start and stored in the memory/config file.
* The user can override AI suggestions (e.g., when a suggested dimension doesn't fit). Corrections can also be provided via PR comments, which the tool can use to refine the mapping.
* Changes to existing mappings are tracked via PR comments (and optionally a changelog as part of the PR).
* If the conversation is interrupted, the user starts over (no session persistence for now).

## 2. What milestones of functionality I want to achieve

### MVP (core proof of concept)
- Conversation interface (basic)
- Accept CSV + DCAT from user
- Generate RML using Azure GPT
- Output targets cube.link + schema.org vocabularies
- Generate dimension hierarchies (DefinedTerm/DefinedTermSet)
- Validate RML against sample data
- Create PR with mapping + human-readable description
- User can override AI suggestions in conversation

### V1 (production-ready)
- Config file for SPARQL endpoint, base URI (stored in GitHub) and other memorable things
- Query existing dimensions/measurements via SPARQL
- Reuse existing dimensions when appropriate
- Preview of datapoints in PR (RDF + human-readable)
- Support adapting existing mappings
- Store dimension RDF files in /dimensions folder
- Refinement from PR: start with PR number, process comments, update mapping

### Later (enhancements)
- Automatic fetching of DCAT metadata from OGD portal APIs (e.g., data.stadt-zuerich.ch)
- Session persistence / conversation recovery
- Changelog as part of PR
- CSV data quality validation (check if CSV is well-formed)
- PR review/approval workflow integration

### Out of scope
- Execute mapping and write RDF (handled by GitHub Action)
- Deploy RDF to triplestore
- DCAT metadata validation (completeness checks)
- Ontology/vocabulary management (uses cube.link + schema.org, doesn't extend them)
- Visualization of data cubes
- Rollback of deployed data
- Chat platform integration (Slack/Teams) - Streamlit provides sufficient chat-like UX



