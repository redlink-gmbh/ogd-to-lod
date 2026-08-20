"""CLI entry point for OGD to LOD tool (core: local CSV + context files)."""

import argparse
import sys

from ogd_to_lod.config import load_config
from ogd_to_lod.logging import get_logger
from ogd_to_lod.runner import run_mapping_session
from urllib.parse import urlparse
from pathlib import Path

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

def main() -> int:
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(
        prog="ogd-to-lod",
        description="Create RML mappings for CSV files using generative AI",
    )
    parser.add_argument(
        "--config",
        "-c",
        default=str(DEFAULT_CONFIG),
        help="Path to configuration file (default: config/config.yaml)",
    )
    parser.add_argument(
        "--base-uri",
        "-b",
        help="Base URI for generated resources (overrides config)",
    )
    parser.add_argument(
        "csv_path",
        nargs="?",
        help="Path to the CSV file to map",
    )
    parser.add_argument(
        "--context",
        "-d",
        nargs="*",
        metavar="FILE",
        dest="context_paths",
        help=(
            "One or more context files describing the dataset "
            "(DCAT, freetext, Markdown, JSON, etc.)"
        ),
    )
    parser.add_argument(
        "--output-folder",
        "-o",
        help=(
            "Folder name within the mappings parent directory where the CSV "
            "and YARRRML files will be pushed."
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

    print("OGD to LOD - RML Mapping Tool")
    print(f"Configuration loaded from: {args.config}")

    if not args.csv_path:
        print("\nUsage: ogd-to-lod <csv_path> --output-folder FOLDER [--context FILE ...]")
        print("For Huwise dataset bootstrap, use the 'ogd-to-lod-huwise' command.")
        print("Run 'ogd-to-lod --help' for more information.")
        return 0

    if not args.output_folder:
        print(
            "Error: --output-folder is required when csv_path is provided",
            file=sys.stderr,
        )
        return 1

    csv_path = args.csv_path
    context_paths = args.context_paths or []
    output_folder = args.output_folder

    if args.dataset_id:
        output_folder = output_folder or args.dataset_id
        huwise_domain = os.environ.get("HUWISE_DOMAIN", "").strip()
        if not huwise_domain:
            print(
                "Error: HUWISE_DOMAIN must be set when using --dataset-id",
                file=sys.stderr,
            )
            return 1
        parsed = urlparse(
            huwise_domain if "://" in huwise_domain else f"https://{huwise_domain}"
        )
        base_url = f"https://{parsed.netloc}/api/explore/v2.1"
        try:
            setup = prepare_dataset_inputs(dataset_id=args.dataset_id, base_url=base_url)
        except DatasetSetupError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        csv_path = setup.csv_path
        context_paths = setup.context_paths
        print(f"\nDataset id: {args.dataset_id}")
        print(f"Setup directory: {setup.setup_dir}")
    else:
        if not output_folder:
            print(
                "Error: --output-folder is required when csv_path is provided",
                file=sys.stderr,
            )
            return 1

    print(f"\nCSV file: {csv_path}")
    for cp in context_paths:
        print(f"Context file: {cp}")

    result = run_mapping_session(
        config,
        csv_path=csv_path,
        context_paths=context_paths,
        base_uri=args.base_uri,
        output_folder=args.output_folder,
        local_output=args.local,
    )
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
