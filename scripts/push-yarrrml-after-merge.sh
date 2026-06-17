#!/usr/bin/env bash
# Push mapping.yarrrml.yaml from a mappings-repository folder layout to Huwise.
#
# Use after a mapping PR is merged to main (mapping/<output-folder>/mapping.yarrrml.yaml).
# Delegates to tests/e2e/push-to-huwise.sh.
#
# Usage:
#   scripts/push-yarrrml-after-merge.sh [--dry-run] --dataset-id ID <mapping-folder>
#
# Example:
#   scripts/push-yarrrml-after-merge.sh --dataset-id 100051 mapping/weather-binningen-hourly

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PUSH="$REPO_ROOT/tests/e2e/push-to-huwise.sh"

DRY_RUN=()
DATASET_ID=""
FOLDER=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=(--dry-run)
      shift
      ;;
    --dataset-id)
      DATASET_ID="${2:-}"
      shift 2
      ;;
    -h|--help)
      sed -n '2,12p' "$0"
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

if [[ -z "$DATASET_ID" ]]; then
  echo "Error: --dataset-id is required" >&2
  exit 1
fi
if [[ -z "$FOLDER" ]]; then
  sed -n '2,12p' "$0" >&2
  exit 1
fi

MAPPING_FILE="$FOLDER/mapping.yarrrml.yaml"
if [[ ! -f "$MAPPING_FILE" ]]; then
  echo "Error: expected $MAPPING_FILE" >&2
  exit 1
fi

exec "$PUSH" "${DRY_RUN[@]}" --dataset-id "$DATASET_ID" --mapping-file "$MAPPING_FILE"
