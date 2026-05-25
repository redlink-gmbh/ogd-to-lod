#!/usr/bin/env bash
# Upload observations.ttl and metadata.ttl from a local results folder to the
# Fuseki instance started by tests/e2e/docker-compose.yml.
#
# Usage: tests/e2e/post-to-fuseki.sh [--clean] <results-folder>
#
# Options:
#   --clean   Drop all existing triples in the dataset (SPARQL CLEAR ALL)
#             before uploading.
#
# The Fuseki endpoint and credentials match the docker-compose service:
#   dataset: test  (FUSEKI_DATASET_1)
#   admin password: admin (ADMIN_PASSWORD)
# Override via FUSEKI_URL / FUSEKI_UPDATE_URL / FUSEKI_USER / FUSEKI_PASSWORD
# env vars if needed.

set -euo pipefail

FUSEKI_URL="${FUSEKI_URL:-http://localhost:3030/test/data}"
FUSEKI_UPDATE_URL="${FUSEKI_UPDATE_URL:-${FUSEKI_URL%/data}/update}"
FUSEKI_USER="${FUSEKI_USER:-admin}"
FUSEKI_PASSWORD="${FUSEKI_PASSWORD:-admin}"

CLEAN=0
FOLDER=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean)
      CLEAN=1
      shift
      ;;
    -h|--help)
      sed -n '2,11p' "$0"
      exit 0
      ;;
    --*)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
    *)
      if [[ -n "$FOLDER" ]]; then
        echo "Error: unexpected extra argument: $1" >&2
        exit 1
      fi
      FOLDER="$1"
      shift
      ;;
  esac
done

if [[ -z "$FOLDER" ]]; then
  echo "Usage: $0 [--clean] <results-folder>" >&2
  exit 1
fi
if [[ ! -d "$FOLDER" ]]; then
  echo "Error: not a directory: $FOLDER" >&2
  exit 1
fi

clear_dataset() {
  echo "→ CLEAR ALL → $FUSEKI_UPDATE_URL"
  curl --silent --show-error --fail \
    --user "$FUSEKI_USER:$FUSEKI_PASSWORD" \
    -H "Content-Type: application/sparql-update" \
    --data-binary "CLEAR ALL" \
    "$FUSEKI_UPDATE_URL"
  echo "  ✓ dataset cleared"
}

post_turtle() {
  local file="$1"
  if [[ ! -f "$file" ]]; then
    echo "Skipping (not found): $file"
    return 0
  fi
  echo "→ POST $file → $FUSEKI_URL"
  curl --silent --show-error --fail \
    --user "$FUSEKI_USER:$FUSEKI_PASSWORD" \
    -H "Content-Type: text/turtle" \
    --data-binary "@$file" \
    "$FUSEKI_URL"
  echo "  ✓ uploaded"
}

if [[ $CLEAN -eq 1 ]]; then
  clear_dataset
fi

post_turtle "$FOLDER/observations.ttl"
post_turtle "$FOLDER/metadata.ttl"

echo "Done. SPARQL the dataset at ${FUSEKI_URL%}"
