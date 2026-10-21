# Sprint Roadmap — Q2 2026

> Planned sprints following MVP completion. Sprint assignments mirror GitHub milestones; backlog items are ordered by `priority-*` labels.

---

## Sprint Hardening (KW 16–18)

**Goal:** Make the system more stable and feature-complete for a first pre-release.

All issues in this sprint carry `priority-1`.

- **#37 — SPARQL-based reuse of existing cube.link properties and DefinedTerms**
  - Align with current workflow (flexible context input, updated graph flow)
  - Ensure tests pass against main branch
  - Merge to main
- **#41 — Generate static RDF triples alongside YARRRML** (cube:Cube, ObservationSet, ObservationConstraint, property definitions)
  - Generate `<mapping>-metadata.ttl` with cube metadata from context
  - Include per-property DimensionConstraint / MeasureConstraint (SHACL-based)
  - Commit to PR alongside YARRRML
- **#44 — Prompt hardening: robustness and edge-case coverage**
  - Systematic testing with diverse CSV/DCAT inputs (edge cases, multilingual, sparse metadata)
  - Guard against prompt injection via CSV content or metadata
  - Improve few-shot examples for better cube.link conformance

---

## Sprint KW 19–21

**Goal:** TBD — milestone created but no issues assigned yet. Likely candidates from the backlog: SHACL shapes (#43), standalone blueprint (#47), output validation (#45).

---

## Backlog (not yet scheduled)

### Priority 2 — High
- **#43 — Generate SHACL shapes for observation validation**
  - Generate SHACL shapes for observation validation
  - Derive constraints from CSV schema + AI proposal (value types, cardinality, patterns)
  - Output as `shapes.ttl` (next to `metadata.ttl`, linked via `cube:observationConstraint`)
  - Newly minted codes get a dataset-scoped `schema:DefinedTermSet` (`schema:inDefinedTermSet`)
- **#47 — Blueprint: Standalone local usage**
  - Minimal example: local CSV + metadata file → YARRRML + static triples
  - Docker-based, no external dependencies beyond Azure OpenAI
  - README with step-by-step usage

### Priority 3 — Medium
- **#45 — Output validation tightening**
  - Stricter YARRRML schema validation before RMLMapper execution
  - Validate static triples against cube.link constraints
  - Better error messages / AI self-repair on validation failure
- **#46 — Blueprint: Huewise Portal integration**
  - Download CSV + DCAT metadata via Huewise API
  - Feed into ogd-to-lod library
  - Upload generated RML skeleton back via API
  - Example script / CLI command with configuration

### Priority 4 — Stretch
- **#48 — Package ogd-to-lod as reusable Python library**
  - Refactor CLI entry points into a clean Python API
  - Publish as installable package (pip / internal registry)
  - Stable public interface for programmatic use
- **#49 — Claude Code skill for autonomous RML generation**
  - Create Claude Code skill definition for ogd-to-lod
  - AI autonomously decides mapping strategy, runs tool, reviews output
  - End-to-end: CSV input → validated YARRRML + static triples → PR, no human in the loop

### Unprioritized
- **#23 — Integrate LangSmith for LLM call tracing**
