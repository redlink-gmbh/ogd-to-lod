"""CLI entry point for OGD to LOD tool."""

import argparse
import sys

from ogd_to_lod.config import load_config
from ogd_to_lod.graph import FlowState, MappingFlow
from ogd_to_lod.logging import get_logger

logger = get_logger(__name__)


def main() -> int:
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(
        prog="ogd-to-lod",
        description="Create RML mappings for CSV files using generative AI",
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
        "csv_path",
        nargs="?",
        help="Path to the CSV file to map",
    )
    parser.add_argument(
        "dcat_path",
        nargs="?",
        help="Path to the DCAT metadata file (JSON-LD or Turtle)",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Path to save the generated RML mapping (Turtle format)",
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
        print("\nUsage: ogd-to-lod <csv_path> [dcat_path]")
        print("Run 'ogd-to-lod --help' for more information.")
        return 0

    print(f"\nCSV file: {args.csv_path}")
    if args.dcat_path:
        print(f"DCAT file: {args.dcat_path}")

    # Start the mapping flow
    try:
        flow = MappingFlow(config)
        state = flow.start(
            csv_path=args.csv_path,
            dcat_path=args.dcat_path,
            base_uri=args.base_uri,
        )
    except Exception as e:
        logger.exception("Failed to start mapping flow")
        print(f"\nError: {e}", file=sys.stderr)
        return 1

    # Check for errors
    if state.current_state == FlowState.ERROR:
        print(f"\nError: {state.error_message}", file=sys.stderr)
        return 1

    # Show parsed summary
    if flow.get_parsed_summary():
        print("\n" + "=" * 60)
        print(flow.get_parsed_summary())

    # Show proposal
    if flow.get_proposal_text():
        print("\n" + "=" * 60)
        print("AI Proposal:")
        print(flow.get_proposal_text())

    # Interactive loop
    while flow.is_awaiting_input() and not flow.is_complete():
        print("\n" + "-" * 60)

        # Show appropriate prompt based on state
        if flow.is_awaiting_pr_confirmation():
            prompt = "Create a PR with this mapping? (yes/no): "
        else:
            prompt = "Your response (or 'quit' to exit): "

        try:
            user_input = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting...")
            return 0

        if user_input.lower() in ("quit", "exit", "q") and not flow.is_awaiting_pr_confirmation():
            print("Exiting...")
            return 0

        if not user_input:
            continue

        try:
            state = flow.continue_with_input(user_input)
        except Exception as e:
            logger.exception("Error processing input")
            print(f"\nError: {e}", file=sys.stderr)
            continue

        # Check for errors
        if state.current_state == FlowState.ERROR:
            print(f"\nError: {state.error_message}", file=sys.stderr)
            return 1

        # Show updated proposal if in refinement
        if flow.get_proposal_text() and state.current_state == FlowState.PROPOSE:
            print("\n" + "=" * 60)
            print("Updated Proposal:")
            print(flow.get_proposal_text())

        # Show generated RML and validation results
        if flow.has_generated_rml():
            print("\n" + "=" * 60)
            print("Generated RML:")
            print("-" * 60)
            print(flow.get_generated_rml())

            # Save RML to file if output path provided
            if args.output:
                try:
                    with open(args.output, 'w', encoding='utf-8') as f:
                        f.write(flow.get_generated_rml())
                    print(f"\n✓ RML saved to: {args.output}")
                except Exception as e:
                    print(f"\n⚠ Warning: Failed to save RML to {args.output}: {e}", file=sys.stderr)

            # Show validation results
            if flow.is_validated():
                print("\n" + "=" * 60)
                print("Validation: PASSED")
                if flow.has_rdf_preview():
                    print("\nRDF Preview (first 2000 chars):")
                    print("-" * 60)
                    preview = flow.get_rdf_preview()[:2000]
                    print(preview)
                    if len(flow.get_rdf_preview()) > 2000:
                        print("... (truncated)")

                # Check if awaiting PR confirmation
                if flow.is_awaiting_pr_confirmation():
                    continue

                # Check if PR was created
                if flow.has_created_pr():
                    print("\n" + "=" * 60)
                    print("PR created successfully!")
                    print(f"PR #{flow.get_pr_number()}: {flow.get_pr_url()}")
                    break

            elif flow.get_validation_error():
                print("\n" + "=" * 60)
                print("Validation: FAILED")
                print(f"Error: {flow.get_validation_error()}")
                print("\nRefining mapping...")
                # Show updated proposal after refinement
                if flow.get_proposal_text():
                    print("\n" + "=" * 60)
                    print("Updated Proposal:")
                    print(flow.get_proposal_text())

        # Check if approved but not yet generated
        if flow.is_approved() and not flow.has_generated_rml():
            print("\nProposal approved! Generating RML...")

        # Check if flow completed (user cancelled PR)
        if flow.is_complete():
            if not flow.has_created_pr() and flow.has_generated_rml():
                print("\n" + "=" * 60)
                print("RML mapping generated but PR creation was skipped.")
                print("You can find the generated RML above.")
            break

    return 0


if __name__ == "__main__":
    sys.exit(main())
