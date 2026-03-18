# OGD to LOD

> **Prototype — version 0.1.** This tool is under active development. Expect rough edges, breaking changes, and incomplete features.

AI-assisted tool that turns Open Government Data (OGD) CSV files into Linked Open Data (LOD) mappings — from raw spreadsheet to a validated YARRRML/RML mapping ready to publish, with minimal manual effort.

## Demo

[![OGD to LOD demo](https://img.youtube.com/vi/AbhaA7YhF3g/0.jpg)](https://www.youtube.com/watch?v=AbhaA7YhF3g)

## What it does

Publishing government data as Linked Open Data requires creating RDF mappings that describe how each CSV column maps to semantic concepts. This is tedious, error-prone, and requires both RDF expertise and deep knowledge of the dataset. **OGD to LOD automates this step.**

Given a CSV file and optional metadata, the tool:

1. **Parses** the CSV (auto-detects encoding and delimiter) and reads any provided context files (DCAT, Markdown, plain text, JSON — any mix)
2. **Normalizes** context using AI into a unified internal model with per-column descriptions, inferring missing descriptions from column names and sample values
3. **Proposes** a mapping structure (dimensions, measures, datatypes) for user review before generating anything
4. **Generates** a YARRRML mapping targeting the [cube.link](https://cube.link) and [schema.org](https://schema.org) vocabularies
5. **Validates** the mapping with a two-tier pipeline: YAML syntax check followed by a Docker-based yarrrml-parser + RMLMapper execution
6. **Opens a GitHub PR** in the target mappings repository with the generated `mapping.yarrrml.yaml` and the CSV source file

The result is a human-reviewable pull request that can be merged, adjusted, or rejected — the AI does the heavy lifting, a human stays in control.

## Installation

### Prerequisites

- Python 3.11+
- Docker (for full two-tier validation with yarrrml-parser and RMLMapper)

### Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/redlink-gmbh/ogd-to-lod.git
   cd ogd-to-lod
   ```

2. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -e ".[dev]"
   ```

4. Configure environment variables:
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

5. Configure the application:
   ```bash
   # Edit config/config.yaml with your settings
   ```

## Configuration

The application uses a YAML configuration file (`config/config.yaml`) with environment variable substitution.

### Required Environment Variables

| Variable | Description |
|----------|-------------|
| `APP_GITHUB_TOKEN` | GitHub Personal Access Token with `repo` scope |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL |
| `AZURE_OPENAI_KEY` | Azure OpenAI API key |

### Optional Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GITHUB_REPO` | Target repository for generated mappings | `redlink-gmbh/ogd-to-lod-mappings` |
| `LOG_LEVEL` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |

### Configuration File

```yaml
github:
  repo: "org/repo-name"
  token: "${APP_GITHUB_TOKEN}"
  mappings_folder: "mapping"  # Parent folder for all mappings (default: mapping)

azure:
  endpoint: "${AZURE_OPENAI_ENDPOINT}"
  api_key: "${AZURE_OPENAI_KEY}"
  deployment: "gpt-4"

sparql:
  endpoint: null  # Optional: for querying existing dimensions

rml:
  base_uri: "https://example.org/resource/"
  rmlmapper_use_docker: true
  rmlmapper_docker_image: "rmlio/rmlmapper-java:latest"
  yarrrml_parser_docker_image: "rmlio/yarrrml-parser:latest"
```

## Usage

```bash
ogd-to-lod <csv_path> --output-folder <folder> [--context FILE ...]
```

### Arguments

| Argument | Description |
|----------|-------------|
| `csv_path` | Path to the CSV file to map (required) |
| `--output-folder FOLDER` | Target subfolder name in the mappings directory (required). The CSV and YARRRML files are pushed to `{mappings_folder}/{output-folder}/` in the repository. |
| `--context FILE [FILE ...]` | One or more context files describing the dataset. Any format is accepted: DCAT (JSON-LD, Turtle, RDF/XML), Markdown, plain text, JSON, or combinations thereof. |

### Options

| Flag | Short | Description |
|------|-------|-------------|
| `--config` | `-c` | Path to configuration file (default: `config/config.yaml`) |
| `--base-uri` | `-b` | Base URI for generated resources (overrides config) |
| `--help` | | Show help message |

### Examples

```bash
# CSV only (no context)
ogd-to-lod data/population.csv --output-folder bev-bestand-2024

# With a DCAT metadata file
ogd-to-lod data/population.csv --output-folder bev-bestand-2024 --context metadata/population.dcat.jsonld

# With multiple context files (DCAT + column documentation)
ogd-to-lod data/population.csv --output-folder bev-bestand-2024 --context metadata/population.dcat.ttl docs/columns.md

# Override base URI
ogd-to-lod data/population.csv --output-folder bev-bestand-2024 --context metadata.ttl --base-uri https://example.org/data/
```

The resulting PR will contain two files in `{mappings_folder}/{output-folder}/`:

- `mapping.yarrrml.yaml` — the generated YARRRML mapping
- `{csv_filename}` — the CSV source file

### Context Files

The `--context` flag accepts any number of files in any format. The AI normalizes all provided
files into a unified internal `DatasetContext` that includes:

- **Dataset-level metadata**: title, description, publisher, keywords, temporal/spatial coverage, license, etc.
- **Column-level metadata**: description and comment per CSV column header

Multiple files are merged — dataset-level fields use the first non-null value (DCAT files take
precedence), while column descriptions are unioned across all files. Columns without explicit
documentation are inferred by the AI from column names and sample values, and surfaced to the
user during the mapping proposal step for review.

## PR Template

The PR description is generated from a Markdown template (`config/pr_template.md`) using `{{placeholder}}` syntax.

### Placeholder Syntax

- `{{Name}}` — replaced with a dynamic value at render time
- `{{Name|default value}}` — uses the default if no value is provided

### Available Placeholders

| Placeholder | Key | Type | Data Source |
|-------------|-----|------|-------------|
| `{{Dataset Name}}` | `dataset_name` | inline | Context title or mapping name |
| `{{Dataset Description}}` | `dataset_description` | inline | Context description |
| `{{CSV Source}}` | `csv_source` | inline | Public CSV URL |
| `{{Context Files}}` | `context_files` | inline | Comma-separated list of all `--context` filenames |
| `{{Base URI}}` | `base_uri` | inline | Base URI from config |
| `{{Mapping Decisions}}` | `mapping_structure` | block | AI proposal (dimensions/measures) |
| `{{CSV Sample}}` | `csv_preview` | block | Parsed CSV sample rows |
| `{{RDF Sample}}` | `rdf_preview` | block | RMLMapper output |

**Inline** placeholders replace only the `{{…}}` token. **Block** placeholders replace the token and all example content below it (up to the next `###` or `---` boundary).

To add a custom placeholder, register it in `_PLACEHOLDER_REGISTRY` in `src/ogd_to_lod/github/pr_template.py`.

## Development

### Running Tests

```bash
pytest
```

### Linting

```bash
ruff check .
ruff format .
```

## Project Structure

```
ogd-to-lod/
├── src/ogd_to_lod/
│   ├── __init__.py
│   ├── cli.py                   # CLI entry point
│   ├── config.py                # Configuration management
│   ├── parsers/
│   │   ├── models.py            # CSVData, DatasetContext, ColumnContext, …
│   │   ├── csv_parser.py        # CSV parsing (encoding/delimiter auto-detect)
│   │   ├── dcat_parser.py       # Deterministic DCAT/RDF parser (rdflib)
│   │   ├── context_parser.py    # Multi-file context reader (format detection)
│   │   └── context_normalizer.py# AI-based extraction → DatasetContext
│   ├── ai/                      # Azure OpenAI integration
│   ├── graph/                   # LangGraph conversation flow
│   ├── rml/                     # YARRRML generation (prompts, AI-driven generator)
│   ├── github/                  # GitHub PR creation (commits mapping.yarrrml.yaml)
│   └── validation/              # Two-tier validation (YAML syntax + Docker: yarrrml-parser → RMLMapper)
├── tests/
├── config/
│   ├── config.yaml
│   └── pr_template.md
├── scripts/                     # Utility scripts (worktrees)
├── pyproject.toml
└── README.md
```

## License

MIT
