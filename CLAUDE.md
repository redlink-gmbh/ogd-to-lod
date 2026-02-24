# Claude Code Project Configuration

## Project Overview

This is the OGD to LOD project - a tool to create RML (RDF Mapping Language) mappings for CSV files using generative AI.

## Allowed Operations

### Git Operations
- `git add`, `git commit`, `git push` - for committing and pushing changes
- `git rm`, `git reset` - for managing staged files

### GitHub CLI
- `gh issue create` - create issues in the repository
- `gh label create` - create labels for issues

### Python Development
- `python3` - run Python scripts
- `pip install` - install dependencies
- `pytest` - run tests
- `ogd-to-lod` - run the CLI tool

### Environment
- GitHub token available via `$APP_GITHUB_TOKEN` environment variable (from `.env`) — this is for the application runtime only
- When acting as Claude Code agent (e.g. using `gh` CLI), use `$GITHUB_TOKEN` from the shell environment directly — never source `.env` or use `APP_GITHUB_TOKEN` for agent actions
- Azure OpenAI credentials via `$AZURE_OPENAI_ENDPOINT` and `$AZURE_OPENAI_KEY`

## Project Structure

```
ogd-to-lod/
├── src/ogd_to_lod/    # Main package
│   ├── parsers/       # CSV and DCAT parsers
│   ├── ai/            # Azure OpenAI integration
│   ├── graph/         # LangGraph conversation flow
│   ├── rml/           # RML generation (prompts, generator)
│   ├── github/        # GitHub PR creation
│   └── validation/    # Two-tier RML validation (syntax + RMLMapper)
├── tests/             # Test files
├── config/            # Configuration files
├── scripts/           # Utility scripts (RMLMapper setup, worktrees)
├── brainstorm.md      # Project requirements
└── architecture.md    # Technical architecture
```

## Key Documentation

- `brainstorm.md` - Project goals, milestones (MVP, V1, Later), and scope
- `architecture.md` - Tech stack, conversation flow, LangGraph states, prompting strategy

## GitHub Repository

- Repository: `redlink-gmbh/ogd-to-lod`
- Issues: MVP issues #1-#10 are created

## Development Workflow

1. Create a feature branch from main (e.g., `feature/issue-2-csv-parser`)
2. Work on issues from the GitHub issue tracker
3. Run tests with `pytest`
4. Commit with descriptive messages referencing issue numbers
5. Push branch and create a Pull Request to main
6. Never commit directly to main branch

### Working on Multiple Issues (Worktrees)

To work on multiple issues in parallel, use git worktrees:

```bash
# Create a new worktree for an issue
./scripts/setup-worktree.sh <issue-number>

# Example: work on issue #12
./scripts/setup-worktree.sh 12
# Creates: ../ogd-to-lod-issue-12 with branch feature/issue-12
```

The script automatically symlinks `.env` and `.claude/` from the main repo.

**Important**: After creating a worktree, open a new Claude Code session in that directory.

```bash
# Clean up when done
git worktree remove ../ogd-to-lod-issue-<number>
```
