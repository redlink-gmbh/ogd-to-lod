# Hackathon Quickstart

Get from a fresh clone to "observations queryable in Fuseki" in about ten
minutes, using Docker for everything.

## What you'll do

1. Bring up the `ogd-to-lod` container and a local Apache Jena Fuseki.
2. Generate a YARRRML mapping + static metadata for a bundled example CSV
   (Basel-Binningen air-quality, hourly).
3. Materialise the mapping into Turtle (`observations.ttl`) with
   yarrrml-parser + RMLMapper.
4. Load the observations and metadata into Fuseki and explore them.

All artifacts land in `results/<timestamp>-weather-binningen-hourly/` on
the host — open the folder in your IDE while the commands are running.

## Prerequisites

- Docker Desktop running (Mac/Windows) or Docker Engine (Linux).
- Azure OpenAI credentials. Copy `.env.example` to `.env` and fill in:
  - `AZURE_OPENAI_ENDPOINT`
  - `AZURE_OPENAI_KEY`
  - `AZURE_OPENAI_DEPLOYMENT`
  - `APP_GITHUB_TOKEN` — any value; only used by the PR path, not by `--local`.
- That's it. No local Python install, no Java, no node.

```bash
cp .env.example .env
# edit .env with your credentials
```

## Step 1 — Build the image and start Fuseki

```bash
docker compose build
docker compose --profile fuseki up -d fuseki
```

Fuseki is now serving an empty dataset `test` at
<http://localhost:3030>. Default credentials: `admin / admin`.

## Step 2 — Generate the mapping with `--local`

The example folder `example/weather-binningen-hourly/` contains a small
CSV plus a DCAT description and a column glossary. Run the full pipeline
against it:

```bash
docker compose run --rm ogd-to-lod \
    example/weather-binningen-hourly/data.csv \
    --output-folder weather-binningen-hourly \
    --context example/weather-binningen-hourly/dcat.ttl \
              example/weather-binningen-hourly/fields.txt \
    --local
```

The tool is interactive — it will:

- show the proposed dimensions/measures and ask you to confirm,
- ask for a dataset name (press Enter to accept the suggested slug),
- ask for a public CSV URL (press Enter to skip as we use local files),
- ask whether to save the results locally (`yes`).

Note: the first run may take a while, as it needs to download the `yarrrml-parser` Docker image.

When it finishes, you'll have a new folder under `results/`:

```
results/<YYYYMMDD-HHMMSS>-weather-binningen-hourly/
├── data.csv
├── mapping.yarrrml.yaml
├── metadata.ttl
└── PR.md
```

Pick that folder's path; you'll reuse it in the next two steps. For
brevity below, set:

```bash
RESULT=$(ls -dt results/*-weather-binningen-hourly | head -1)
echo "$RESULT"
```

## Step 3 — Materialise RDF (`observations.ttl`)

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

## Step 4 — Load everything into Fuseki

```bash
tests/e2e/post-to-fuseki.sh --clean "$RESULT"
```

`--clean` issues a SPARQL `CLEAR ALL` first so you can re-run from a fresh
state. `metadata.ttl` (the static cube + per-property descriptions) and
`observations.ttl` (the per-row triples) are uploaded via Fuseki's Graph
Store Protocol.

## Step 5 — Explore

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

## Step 6 — Browse the RDF in a Linked-Data viewer (optional)

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

The bundled example uses `https://ld.domain.ch/statistics/...` as the
base. Trifid's `DATASET_BASE_URL` is set to that host by default in
`docker-compose.yml`, so a request to

```
http://localhost:8080/statistics/weather-binningen-hourly
```

is rewritten to a SPARQL `DESCRIBE` against
`<https://ld.domain.ch/statistics/weather-binningen-hourly>` and you get
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
DESCRIBE <https://ld.domain.ch/statistics/weather-binningen-hourly>
```

That returns the same triple set Trifid renders, just without the HTML
chrome.

## Iterating

- Edit `mapping.yarrrml.yaml` by hand in the result folder, then re-run
  Step 3 and Step 4 to see the changes in Fuseki.
- `--clean` on `post-to-fuseki.sh` resets the dataset between iterations.
- To start over with a different CSV, drop one into a sub-folder under
  `example/` (or anywhere in the repo) and re-run Step 2 with the new
  paths and `--output-folder`.

## Tearing down
Stops Fuseki + Trifid (if running) and drops Fuseki's data volume
```bash
docker compose --profile fuseki --profile trifid down -v
```

The `results/` folder is on the host filesystem; delete it manually if
you want to start from a clean tree.

## When something goes wrong

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
