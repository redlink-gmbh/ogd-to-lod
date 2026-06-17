#!/usr/bin/env bash
# Push YARRRML to Huwise semantic.rml_mapping via the Automation API.
#
# Usage:
#   tests/e2e/push-to-huwise.sh [--dry-run] [--check] (--dataset-id ID | --dataset-uid UID)
#       (<results-folder> | --mapping-file PATH)
#
# Optional semantic fields (if files exist or paths given):
#   [--classes-file PATH] [--properties-file PATH]
#
# By default the mapping is transformed for Huwise (drops sources/{CSV_SOURCE},
# rewrites $(label) → $(technical_field_name)). Pass --raw to push the file as-is.
#
# Environment (or repo-root .env):
#   HUWISE_DOMAIN   e.g. data.bs.ch
#   HUWISE_API_KEY  Automation API key with dataset edit rights
#
# Examples:
#   tests/e2e/push-to-huwise.sh --check
#   tests/e2e/push-to-huwise.sh --dataset-id 100051 results/<timestamp>-weather-binningen-hourly
#   tests/e2e/push-to-huwise.sh --dataset-uid da_wn1p7t --mapping-file mapping/slug/mapping.yarrrml.yaml

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

load_env() {
  if [[ -f "$REPO_ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +a
  fi
}

usage() {
  sed -n '2,18p' "$0"
}

normalize_domain() {
  local d="${1%/}"
  d="${d#https://}"
  d="${d#http://}"
  echo "$d"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Error: required command not found: $1" >&2
    exit 1
  fi
}

json_value_payload() {
  local file="$1"
  python3 -c 'import json, sys; print(json.dumps({"value": open(sys.argv[1], encoding="utf-8").read()}))' "$file"
}

json_null_payload() {
  python3 -c 'import json; print(json.dumps({"value": None}))'
}

resolve_dataset_uid() {
  local domain="$1"
  local dataset_id="$2"
  local explore_url="https://${domain}/api/explore/v2.1/catalog/datasets/${dataset_id}"
  local body
  body="$(curl --silent --show-error --fail "$explore_url")"
  python3 -c 'import json, sys; d=json.load(sys.stdin); print(d["dataset_uid"])' <<<"$body"
}

resolve_dataset_id() {
  local automation_base="$1"
  local auth_header="$2"
  local dataset_uid="$3"
  local url="${automation_base}/datasets/${dataset_uid}/"
  local body
  body="$(curl --silent --show-error --fail -H "$auth_header" "$url")"
  python3 -c 'import json, sys; d=json.load(sys.stdin); print(d["dataset_id"])' <<<"$body"
}

verify_tpf_rdf() {
  local domain="$1"
  local dataset_id="$2"
  local url="https://${domain}/api/tpf/${dataset_id}/"
  echo "→ Verify RDF via TPF ${url}"
  local sample
  sample="$(curl --silent --show-error --max-time 20 "$url" 2>/dev/null | head -c 8192 || true)"
  if echo "$sample" | grep -qE '/observation/|Observation'; then
    echo "  ✓ TPF returns RDF triples"
    return 0
  fi
  echo "  ✗ TPF sample has no observations — check semantic.rml_mapping" >&2
  return 1
}

prepare_mapping_for_huwise() {
  local mapping_file="$1"
  local domain="$2"
  local dataset_id="$3"
  local output_file="$4"
  if ! python3 -c 'import yaml' 2>/dev/null; then
    echo "Error: PyYAML required on the host for mapping preparation." >&2
    echo "  pip install pyyaml   # or: pip install -e ." >&2
    exit 1
  fi
  echo "→ Prepare mapping for Huwise (drop sources/CSV_SOURCE, use field names)"
  python3 "${SCRIPT_DIR}/prepare_mapping_for_huwise.py" \
    "$mapping_file" \
    --domain "$domain" \
    --dataset-id "$dataset_id" \
    -o "$output_file"
}

check_semantic_template() {
  local automation_base="$1"
  local auth_header="$2"
  local url="${automation_base}/metadata/templates/semantic/fields/"
  echo "→ GET semantic template fields → ${url}"
  local body
  body="$(curl --silent --show-error --fail -H "$auth_header" "$url")"
  HUWISE_CHECK_BODY="$body" python3 -c '
import json
import os

fields = json.loads(os.environ["HUWISE_CHECK_BODY"])
if isinstance(fields, list):
    items = fields
elif isinstance(fields, dict) and "results" in fields:
    items = fields["results"]
else:
    items = list(fields.values()) if isinstance(fields, dict) else [fields]
names = []
for item in items:
    if not isinstance(item, dict):
        continue
    name = item.get("name") or item.get("field_name")
    if not name:
        continue
    names.append(name)
    if name == "rml_mapping":
        print(
            "  rml_mapping: type=%s, requirement=%s"
            % (item.get("type", "?"), item.get("requirement_level", "?"))
        )
if "rml_mapping" not in names:
    print("  WARNING: rml_mapping field not listed — semantic template may be inactive")
else:
    print("  OK: semantic template exposes rml_mapping")
'
}

put_metadata_field() {
  local dry_run="$1"
  local auth_header="$2"
  local url="$3"
  local payload="$4"
  local label="$5"
  local bytes
  bytes="$(printf '%s' "$payload" | wc -c | tr -d ' ')"
  echo "→ PUT ${label} (${bytes} bytes JSON) → ${url}"
  if [[ "$dry_run" -eq 1 ]]; then
    echo "  (dry-run: skipped)"
    return 0
  fi
  curl --silent --show-error --fail \
    -X PUT \
    -H "$auth_header" \
    -H "Content-Type: application/json" \
    --data-binary "$payload" \
    "$url"
  echo "  ✓ ${label} updated"
}

DRY_RUN=0
CHECK_ONLY=0
RAW_UPLOAD=0
DATASET_ID=""
DATASET_UID=""
FOLDER=""
MAPPING_FILE=""
CLASSES_FILE=""
PROPERTIES_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --raw)
      RAW_UPLOAD=1
      shift
      ;;
    --check)
      CHECK_ONLY=1
      shift
      ;;
    --dataset-id)
      DATASET_ID="${2:-}"
      shift 2
      ;;
    --dataset-uid)
      DATASET_UID="${2:-}"
      shift 2
      ;;
    --mapping-file)
      MAPPING_FILE="${2:-}"
      shift 2
      ;;
    --classes-file)
      CLASSES_FILE="${2:-}"
      shift 2
      ;;
    --properties-file)
      PROPERTIES_FILE="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      echo "Unknown option: $1" >&2
      usage >&2
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

load_env
require_cmd curl
require_cmd python3

HUWISE_DOMAIN="$(normalize_domain "${HUWISE_DOMAIN:-}")"
if [[ -z "$HUWISE_DOMAIN" ]]; then
  echo "Error: HUWISE_DOMAIN is not set (e.g. data.bs.ch)" >&2
  exit 1
fi
if [[ -z "${HUWISE_API_KEY:-}" ]]; then
  echo "Error: HUWISE_API_KEY is not set" >&2
  exit 1
fi

AUTOMATION_BASE="https://${HUWISE_DOMAIN}/api/automation/v1.0"
AUTH_HEADER="Authorization: ApiKey ${HUWISE_API_KEY}"

if [[ $CHECK_ONLY -eq 1 ]]; then
  check_semantic_template "$AUTOMATION_BASE" "$AUTH_HEADER"
  exit 0
fi

if [[ -n "$MAPPING_FILE" && -n "$FOLDER" ]]; then
  echo "Error: pass either a results folder or --mapping-file, not both" >&2
  exit 1
fi

if [[ -n "$MAPPING_FILE" ]]; then
  if [[ ! -f "$MAPPING_FILE" ]]; then
    echo "Error: mapping file not found: $MAPPING_FILE" >&2
    exit 1
  fi
  MAPPING="$MAPPING_FILE"
  if [[ -z "$CLASSES_FILE" && -f "$(dirname "$MAPPING_FILE")/semantic.classes" ]]; then
    CLASSES_FILE="$(dirname "$MAPPING_FILE")/semantic.classes"
  fi
  if [[ -z "$PROPERTIES_FILE" && -f "$(dirname "$MAPPING_FILE")/semantic.properties" ]]; then
    PROPERTIES_FILE="$(dirname "$MAPPING_FILE")/semantic.properties"
  fi
elif [[ -n "$FOLDER" ]]; then
  if [[ ! -d "$FOLDER" ]]; then
    echo "Error: not a directory: $FOLDER" >&2
    exit 1
  fi
  MAPPING="$FOLDER/mapping.yarrrml.yaml"
  if [[ ! -f "$MAPPING" ]]; then
    echo "Error: missing $MAPPING" >&2
    exit 1
  fi
  if [[ -z "$CLASSES_FILE" && -f "$FOLDER/semantic.classes" ]]; then
    CLASSES_FILE="$FOLDER/semantic.classes"
  fi
  if [[ -z "$PROPERTIES_FILE" && -f "$FOLDER/semantic.properties" ]]; then
    PROPERTIES_FILE="$FOLDER/semantic.properties"
  fi
else
  echo "Error: pass a results folder or --mapping-file" >&2
  exit 1
fi

if [[ -z "$DATASET_ID" && -z "$DATASET_UID" ]]; then
  echo "Error: pass --dataset-id or --dataset-uid" >&2
  exit 1
fi
if [[ -n "$DATASET_ID" && -n "$DATASET_UID" ]]; then
  echo "Error: use only one of --dataset-id or --dataset-uid" >&2
  exit 1
fi

if [[ -z "$DATASET_UID" ]]; then
  echo "→ Resolve dataset_uid for dataset_id=${DATASET_ID}"
  DATASET_UID="$(resolve_dataset_uid "$HUWISE_DOMAIN" "$DATASET_ID")"
  echo "  uid: ${DATASET_UID}"
elif [[ -z "$DATASET_ID" ]]; then
  echo "→ Resolve dataset_id for dataset_uid=${DATASET_UID}"
  DATASET_ID="$(resolve_dataset_id "$AUTOMATION_BASE" "$AUTH_HEADER" "$DATASET_UID")"
  echo "  id: ${DATASET_ID}"
fi

PREPARED_MAPPING=""
cleanup_prepared() {
  if [[ -n "$PREPARED_MAPPING" && -f "$PREPARED_MAPPING" ]]; then
    rm -f "$PREPARED_MAPPING"
  fi
}
trap cleanup_prepared EXIT

if [[ $RAW_UPLOAD -eq 0 ]]; then
  if [[ -z "$DATASET_ID" ]]; then
    echo "Error: --dataset-id is required for Huwise mapping preparation (or use --raw)" >&2
    exit 1
  fi
  PREPARED_MAPPING="$(mktemp "${TMPDIR:-/tmp}/huwise-mapping.XXXXXX")"
  prepare_mapping_for_huwise "$MAPPING" "$HUWISE_DOMAIN" "$DATASET_ID" "$PREPARED_MAPPING"
  MAPPING="$PREPARED_MAPPING"
fi

META_BASE="${AUTOMATION_BASE}/datasets/${DATASET_UID}/metadata/semantic"

put_metadata_field "$DRY_RUN" "$AUTH_HEADER" \
  "${META_BASE}/rml_mapping/" \
  "$(json_value_payload "$MAPPING")" \
  "semantic/rml_mapping"

if [[ -n "$CLASSES_FILE" ]]; then
  if [[ ! -f "$CLASSES_FILE" ]]; then
    echo "Error: classes file not found: $CLASSES_FILE" >&2
    exit 1
  fi
  put_metadata_field "$DRY_RUN" "$AUTH_HEADER" \
    "${META_BASE}/classes/" \
    "$(json_value_payload "$CLASSES_FILE")" \
    "semantic/classes"
fi

if [[ -n "$PROPERTIES_FILE" ]]; then
  if [[ ! -f "$PROPERTIES_FILE" ]]; then
    echo "Error: properties file not found: $PROPERTIES_FILE" >&2
    exit 1
  fi
  put_metadata_field "$DRY_RUN" "$AUTH_HEADER" \
    "${META_BASE}/properties/" \
    "$(json_value_payload "$PROPERTIES_FILE")" \
    "semantic/properties"
fi

PUBLISH_URL="${AUTOMATION_BASE}/datasets/${DATASET_UID}/publish_metadata/"
echo "→ POST publish_metadata → ${PUBLISH_URL}"
if [[ $DRY_RUN -eq 1 ]]; then
  echo "  (dry-run: skipped)"
else
  curl --silent --show-error --fail \
    -X POST \
    -H "$AUTH_HEADER" \
    "$PUBLISH_URL"
  echo "  ✓ metadata published"
  verify_tpf_rdf "$HUWISE_DOMAIN" "$DATASET_ID"
fi

echo "Done. RDF (TPF): https://${HUWISE_DOMAIN}/api/tpf/${DATASET_ID}/"
echo "  Full export may be large; prefer TPF over exports/turtle for a quick check."
