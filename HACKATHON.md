# Hackathon Quickstart

Get from a fresh clone to **live RDF on Huwise** or **observations in local
Fuseki** in about ten minutes. Mapping generation runs in Docker; Huwise push
runs on your machine (see prerequisites).

## What you'll do

1. Bring up the `ogd-to-lod` container (and optionally Fuseki).
2. Generate a YARRRML mapping + static metadata (bundled CSV example, or
   `--dataset-id` from Huwise).
3. **Huwise path** — push the mapping to the portal so RDF/TPF works on the
   live dataset (do this right after step 2 when using `--dataset-id`).
4. **Local path (optional)** — materialise Turtle with yarrrml-parser +
   RMLMapper and load into Fuseki for SPARQL on your laptop.

All artifacts land in `results/<timestamp>-<slug>/` on the host — open the
folder in your IDE while the commands are running.

## Prerequisites

- Docker Desktop running (Mac/Windows) or Docker Engine (Linux).
- Azure OpenAI credentials. Copy `.env.example` to `.env` and fill in:
  - `AZURE_OPENAI_ENDPOINT`
  - `AZURE_OPENAI_KEY`
  - `AZURE_OPENAI_DEPLOYMENT`
  - `APP_GITHUB_TOKEN` — any value; only used by the PR path, not by `--local`.
- For dataset bootstrap mode (`--dataset-id`) and Huwise push, also set:
  - `HUWISE_DOMAIN` (for example `data.bs.ch`)
  - `HUWISE_API_KEY` — Automation API key with edit rights on the dataset
- **Host tools for Huwise push only** (mapping generation still uses Docker):
  - `python3`, `curl`, and PyYAML (`pip install pyyaml` or `pip install -e .`
    from the repo root)
- No local Java or Node required for the default flow.

```bash
cp .env.example .env
# edit .env with your credentials
pip install -e .   # optional but recommended if push-to-huwise fails on "import yaml"
```

## Step 1 — Build the image and start Fuseki

```bash
docker compose build
docker compose --profile fuseki up -d fuseki
```

Fuseki is now serving an empty dataset `test` at
<http://localhost:3030>. Default credentials: `admin / admin`.

## Step 2 — Generate the mapping with `--local`

Preferred path: run with dataset bootstrap (`--dataset-id`) so CSV and
metadata are fetched automatically:

```bash
docker compose run --rm ogd-to-lod \
    --dataset-id 100051 \
    --local
```

Alternative path: run against the local bundled files:

```bash
docker compose run --rm ogd-to-lod \
    example/weather-binningen-hourly/data.csv \
    --output-folder weather-binningen-hourly \
    --context example/weather-binningen-hourly/dcat.ttl \
              example/weather-binningen-hourly/fields.txt \
    --local
```

In dataset mode, setup artifacts are materialized under
`.work/dataset_setup/<timestamp>-100051/` and then fed into the same
workflow.

The tool is interactive — it will:

- show the proposed dimensions/measures and ask you to confirm,
- ask for a dataset name (press Enter to accept the suggested slug),
- ask for a public CSV URL (press Enter to skip as we use local files),
- ask whether to save the results locally (`yes`).

Note: the first run may take a while, as it needs to download the `yarrrml-parser` Docker image.

When it finishes, you'll have a new folder under `results/`:

```
results/<YYYYMMDD-HHMMSS>-<slug>/
├── data.csv
├── mapping.yarrrml.yaml
├── metadata.ttl
└── PR.md
```

Pick that folder's path; you'll reuse it below. For brevity:

```bash
# Latest results folder (adjust the glob if you used --dataset-id 100051)
RESULT=$(ls -dt results/* | head -1)
echo "$RESULT"
```

## Two paths after Step 2

| Goal | Next step |
|------|-----------|
| **RDF on Huwise** (live portal / TPF) | [Step 3 — Push to Huwise](#step-3--push-to-huwise-dataset-id) |
| **RDF on your laptop** (Fuseki) | [Step 4 — Materialise](#step-4--materialise-rdf-observationsttl) then Step 5 |

When you used `--dataset-id`, do **Step 3 first** — Huwise does not use
`{CSV_SOURCE}` or local `data.csv`; the push script converts ogd-to-lod
YARRRML into the [Huwise mapping dialect](https://help.opendatasoft.com/apis/tpf)
(no `sources:`, full IRIs, `predicateobjects:`, `$(technical_field_name)`).

## Step 3 — Push to Huwise (`--dataset-id`)

Requires `HUWISE_DOMAIN` and `HUWISE_API_KEY` in `.env`.

```bash
tests/e2e/push-to-huwise.sh --check   # optional: verify semantic/rml_mapping field
tests/e2e/push-to-huwise.sh --dataset-id 100051 "$RESULT"
```

Dry-run (resolve uid + show URLs, no writes):

```bash
tests/e2e/push-to-huwise.sh --dry-run --dataset-id 100051 "$RESULT"
```

This uses the [Automation API](https://developer.huwise.com/apis/automation/v1.0/index.html)
(`PUT .../metadata/semantic/rml_mapping/` then `POST .../publish_metadata/`).
The script prepares the mapping (drops `{CSV_SOURCE}` / `sources:`, rewrites
labels → technical field names, expands prefixes to full IRIs). Use `--raw`
only for debugging.

After a successful push, the script checks the [TPF API](https://help.opendatasoft.com/apis/tpf).
You can also open:

`https://<HUWISE_DOMAIN>/api/tpf/<DATASET_ID>/`

Example: `https://data.bs.ch/api/tpf/100051/` — look for observation IRIs and
literal measures (`o3_ug_m3`, etc.). Full `exports/turtle` can be very large
and slow to download; prefer TPF for a quick check.

## Step 4 — Materialise RDF (`observations.ttl`)

The helper script substitutes the `{CSV_SOURCE}` placeholder, runs
yarrrml-parser to convert YARRRML → RML, then RMLMapper to execute the
mapping against `data.csv`, and writes `observations.ttl` back into the
result folder:

```bash
tests/e2e/run-mapping.sh "$RESULT"
ls "$RESULT/observations.ttl"
```

(The script uses sibling Docker containers — it'll pull
`rmlio/yarrrml-parser` and `rmlio/rmlmapper-java` the first time.)

## Step 5 — Load everything into Fuseki

```bash
tests/e2e/post-to-fuseki.sh --clean "$RESULT"
```

`--clean` issues a SPARQL `CLEAR ALL` first so you can re-run from a fresh
state. `metadata.ttl` (the static cube + per-property descriptions) and
`observations.ttl` (the per-row triples) are uploaded via Fuseki's Graph
Store Protocol.

## Step 6 — Explore

Open the Fuseki UI: <http://localhost:3030/#/dataset/test/query>.

Try these queries:

```sparql
# Which cubes are there?
PREFIX cube: <https://cube.link/>
SELECT DISTINCT ?cube WHERE { ?cube a cube:Cube }
```

```sparql
# How many observations?
PREFIX cube: <https://cube.link/>
SELECT (COUNT(?o) AS ?n) WHERE { ?o a cube:Observation }
```

```sparql
# Property catalogue with descriptions
PREFIX cube:   <https://cube.link/>
PREFIX schema: <http://schema.org/>
SELECT ?prop ?kind ?name ?description WHERE {
  ?prop a ?kind ; schema:name ?name .
  FILTER(?kind IN (cube:KeyDimension, cube:MeasureDimension))
  OPTIONAL { ?prop schema:description ?description }
}
```

```sparql
# All triples for one observation
PREFIX cube: <https://cube.link/>
SELECT ?p ?o WHERE { ?obs a cube:Observation ; ?p ?o } LIMIT 50
```

## Step 7 — Browse the RDF in a Linked-Data viewer (optional)

Fuseki's SPARQL UI is fine for queries; for click-through *browsing* of
each cube / observation / property as an HTML page, bring up **Trifid**:

```bash
docker compose --profile trifid up -d trifid
```

Trifid is now serving the dataset at <http://localhost:8080/>. It comes
preconfigured with:

- **`/`** — root page; clickable index.
- **`/sparql/`** — YASGUI SPARQL editor (the same SPARQL backend, prettier
  UI than Fuseki's).
- **`/graph-explorer/`** — visual graph exploration.
- **`/spex/`** — schema explorer.
- **`/query`** — SPARQL endpoint (proxied to Fuseki).

### Dereferencing your cube IRIs

The bundled example uses `https://ld.bs.ch/...` as the
base. Trifid's `DATASET_BASE_URL` is set to that host by default in
`docker-compose.yml`, so a request to

```
http://localhost:8080/statistics/weather-binningen-hourly
```

is rewritten to a SPARQL `DESCRIBE` against
`<https://ld.bs.ch/weather-binningen-hourly>` and you get
the cube's full set of triples rendered as HTML. Same trick for any
observation, property, or DefinedTerm IRI.

If you change the project's `base_uri` to something else (e.g.
`https://example.org/`), pass it through when bringing Trifid up:

```bash
DATASET_BASE_URL=https://example.org/ docker compose --profile trifid up -d trifid
```

### No-extra-container fallback

If you just want to inspect a specific resource without running Trifid,
Fuseki's SPARQL UI handles `DESCRIBE` natively:

```sparql
DESCRIBE <https://ld.bs.ch/weather-binningen-hourly>
```

That returns the same triple set Trifid renders, just without the HTML
chrome.

## Iterating

- **Huwise:** edit `mapping.yarrrml.yaml`, then re-run Step 3 (`push-to-huwise.sh`).
- **Fuseki:** edit the mapping, then re-run Step 4 and Step 5.
- `--clean` on `post-to-fuseki.sh` resets the local dataset between iterations.
- To start over with a different CSV or dataset id, re-run Step 2.

## Tearing down
Stops Fuseki + Trifid (if running) and drops Fuseki's data volume
```bash
docker compose --profile fuseki --profile trifid down -v
```

The `results/` folder is on the host filesystem; delete it manually if
you want to start from a clean tree.

## When something goes wrong

- `import yaml` / PyYAML error when pushing to Huwise — install host deps:
  `pip install -e .` from the repo root.
- Push succeeds but TPF has no observations — ensure `--dataset-id` matches
  the portal dataset and field refs use Huwise technical names (`datum_zeit`,
  not `Datum/Zeit`); re-push without `--raw`.
- `[Errno 2] No such file or directory: 'docker'` inside the container —
  the image is stale; rebuild with `docker compose build --no-cache`.
- Fuseki refuses uploads with `403 Forbidden` — credentials drifted from
  the docker-compose defaults; override with
  `FUSEKI_USER=... FUSEKI_PASSWORD=... tests/e2e/post-to-fuseki.sh ...`.
- The CLI hangs at a prompt — `docker compose run --rm` keeps stdin/tty
  attached, but some terminals (older Windows shells, CI logs) drop the
  TTY. Use a real terminal.
- `Connection refused` on `http://localhost:3030` — Fuseki container
  isn't up yet; check with `docker compose ps fuseki`. First-time start
  takes a few seconds.
