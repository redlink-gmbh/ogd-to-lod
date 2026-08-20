"""CLI entry point for Huwise mode: download dataset, then run the mapping flow."""

import argparse
import os
import sys

from ogd_to_lod.config import load_config
from ogd_to_lod.huwise.download import (
    DatasetSetupError,
    prepare_dataset_inputs,
    resolve_base_url,
)
from ogd_to_lod.huwise.upload import HuwiseUploadError, upload_results
from ogd_to_lod.logging import get_logger
from ogd_to_lod.runner import run_mapping_session

logger = get_logger(__name__)


def main() -> int:
    """Entry point for the `ogd-to-lod-huwise` command."""
    parser = argparse.ArgumentParser(
        prog="ogd-to-lod-huwise",
        description=(
            "Bootstrap CSV + metadata from a Huwise (OpenDataSoft) dataset, "
            "then run the ogd-to-lod mapping flow"
        ),
    )
    parser.add_argument(
        "--config",
        "-c",
        default="config/config.yaml",
        help="Path to configuration file (default: config/config.yaml)",
    )
    parser.add_argument(
        "--base-uri",
        "-b",
        help="Base URI for generated resources (overrides config)",
    )
    parser.add_argument(
        "--dataset-id",
        required=True,
        help="Dataset identifier to bootstrap CSV and metadata from the Huwise API",
    )
    parser.add_argument(
        "--output-folder",
        "-o",
        help=(
            "Folder name within the mappings parent directory for the CSV and "
            "YARRRML files. Defaults to the dataset id"
        ),
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help=(
            "Write the generated files and the PR description to a local "
            "'results/<timestamp>-<output-folder>/' folder instead of "
            "opening a GitHub PR"
        ),
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload the generated results back to Huwise (not implemented yet)",
    )
    args = parser.parse_args()

    # Load configuration
    try:
        config = load_config(args.config)
    except FileNotFoundError:
        print(f"Error: Configuration file not found: {args.config}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Error: Invalid configuration: {e}", file=sys.stderr)
        return 1

    print("OGD to LOD - Huwise Mode")
    print(f"Configuration loaded from: {args.config}")

    output_folder = args.output_folder or args.dataset_id

    # Resolve the Huwise API base URL from the HUWISE_DOMAIN env var
    huwise_domain = os.environ.get("HUWISE_DOMAIN", "").strip()
    if not huwise_domain:
        print(
            "Error: HUWISE_DOMAIN must be set to use ogd-to-lod-huwise",
            file=sys.stderr,
        )
        return 1

    try:
        base_url = resolve_base_url(huwise_domain)
        setup = prepare_dataset_inputs(dataset_id=args.dataset_id, base_url=base_url, config=config)
    except DatasetSetupError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"\nDataset id: {args.dataset_id}")
    print(f"Setup directory: {setup.setup_dir}")
    print(f"\nCSV file: {setup.csv_path}")
    for cp in setup.context_paths:
        print(f"Context file: {cp}")

    result = run_mapping_session(
        config,
        csv_path=setup.csv_path,
        context_paths=setup.context_paths,
        base_uri=args.base_uri,
        output_folder=output_folder,
        local_output=args.local,
    )

    # Upload seam: wired end-to-end but inert until implemented.
    if args.upload and result.exit_code == 0:
        try:
            upload_results(result, config, args.dataset_id)
        except NotImplementedError as e:
            print(f"\nUpload skipped: {e}", file=sys.stderr)
        except HuwiseUploadError as e:
            print(f"\nUpload failed: {e}", file=sys.stderr)
            return 1

    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
