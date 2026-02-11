# OGD to LOD

Tool to create RML (RDF Mapping Language) mappings for CSV files using generative AI.

## Overview

This tool helps transform Open Government Data (OGD) CSV files into Linked Open Data (LOD) by:

1. Analyzing CSV structure and DCAT metadata
2. Using AI to propose RML mappings targeting cube.link and schema.org vocabularies
3. Validating mappings with RMLMapper (two-tier: syntax check + data-aware execution)
4. Creating GitHub PRs with the generated mappings

## Installation

### Prerequisites

- Python 3.11+
- Java (optional, for RMLMapper validation)

To set up RMLMapper for full validation:
```bash
./scripts/setup-rmlmapper.sh
```

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
| `GITHUB_TOKEN` | GitHub Personal Access Token with `repo` scope |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL |
| `AZURE_OPENAI_KEY` | Azure OpenAI API key |

### Optional Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GITHUB_REPO` | Target repository for generated mappings | `redlink-gmbh/ogd-to-lod-mappings` |
| `RMLMAPPER_JAR` | Path to RMLMapper JAR file | `tools/rmlmapper.jar` |
| `LOG_LEVEL` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |

### Configuration File

```yaml
github:
  repo: "org/repo-name"
  token: "${GITHUB_TOKEN}"

azure:
  endpoint: "${AZURE_OPENAI_ENDPOINT}"
  api_key: "${AZURE_OPENAI_KEY}"
  deployment: "gpt-4"

sparql:
  endpoint: null  # Optional: for querying existing dimensions

rml:
  base_uri: "https://example.org/resource/"
```

## Usage

```bash
ogd-to-lod <csv_path> <dcat_path>
```

### Options

- `--config`, `-c`: Path to configuration file (default: `config/config.yaml`)
- `--output`, `-o`: Path to save the generated RML mapping (Turtle format)
- `--base-uri`, `-b`: Base URI for generated resources (overrides config)
- `--help`: Show help message

## PR Template

The PR description is generated from a Markdown template (`config/pr_template.md`) using `{{placeholder}}` syntax.

### Placeholder Syntax

- `{{Name}}` — replaced with a dynamic value at render time
- `{{Name|default value}}` — uses the default if no value is provided

### Available Placeholders

| Placeholder | Key | Type | Data Source |
|-------------|-----|------|-------------|
| `{{Dataset Name}}` | `dataset_name` | inline | DCAT title or mapping name |
| `{{Dataset Description}}` | `dataset_description` | inline | DCAT description |
| `{{CSV Source}}` | `csv_source` | inline | Public CSV URL |
| `{{DCAT Source}}` | `dcat_source` | inline | Public DCAT metadata URL |
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
│   ├── cli.py           # CLI entry point
│   ├── config.py        # Configuration management
│   ├── parsers/         # CSV and DCAT parsers
│   ├── ai/              # Azure OpenAI integration
│   ├── graph/           # LangGraph conversation flow
│   ├── rml/            # RML generation (prompts, AI-driven generator)
│   ├── github/          # GitHub PR creation
│   └── validation/      # Two-tier RML validation (syntax + RMLMapper + empty-output detection)
├── tests/
├── config/
│   └── config.yaml
├── scripts/             # Utility scripts (RMLMapper setup, worktrees)
├── pyproject.toml
└── README.md
```

## License

MIT
