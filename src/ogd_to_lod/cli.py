"""CLI entry point for OGD to LOD tool."""

import argparse
import sys

from ogd_to_lod.ai import RequestLimitReached, TokenUsage
from ogd_to_lod.config import load_config
from ogd_to_lod.graph import FlowState, MappingFlow
from ogd_to_lod.logging import get_logger

logger = get_logger(__name__)


def format_token_stats(flow: MappingFlow) -> str:
    """Format token usage statistics as a string.

    Args:
        flow: MappingFlow instance with AI service.

    Returns:
        Formatted token statistics string.
    """
    ai = flow.ai_service
    usage = ai.token_usage
    cost = ai.get_total_cost()

    lines = []
    lines.append(f"Requests: {ai.request_count}/{ai.request_limit}")
    lines.append(
        f"Tokens: {usage.total_tokens:,} "
        f"({usage.input_tokens:,} in, {usage.output_tokens:,} out"
    )
    if usage.cached_tokens > 0:
        lines[-1] += f", {usage.cached_tokens:,} cached"
    lines[-1] += ")"
    lines.append(f"Cost: CHF {cost:.4f}")

    return " | ".join(lines)


def create_token_callback(request_limit: int) -> callable:
    """Create a callback that prints token stats in real-time.

    Args:
        request_limit: Maximum request limit for display.

    Returns:
        Callback function.
    """
    def callback(
        request_count: int,
        last_tokens: TokenUsage,
        total_tokens: TokenUsage,
        total_cost: float,
    ) -> None:
        """Print token usage update."""
        # Format: [Req 5/50 | +1,234 tok | Total: 12,345 | CHF 0.0580]
        msg = f"  → Req {request_count}/{request_limit}"
        msg += f" | +{last_tokens.total_tokens:,} tok"
        msg += f" | Total: {total_tokens.total_tokens:,}"
        msg += f" | CHF {total_cost:.4f}"
        print(msg, flush=True)

    return callback


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

        # Register callback for real-time token updates
        token_callback = create_token_callback(config.azure.max_requests)
        flow.ai_service.register_token_callback(token_callback)

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
        if flow.is_awaiting_name_confirmation():
            name = flow.state.mapping_name or "mapping"
            prompt = f"Dataset name ['{name}']: "
        elif flow.is_awaiting_csv_url():
            prompt = "Public CSV source URL (Enter to skip): "
        elif flow.is_awaiting_dcat_url():
            prompt = "Public DCAT metadata URL (Enter to skip): "
        elif flow.is_awaiting_dcat_inclusion():
            prompt = "Include DCAT metadata file in PR? (yes/no): "
        elif flow.is_awaiting_pr_confirmation():
            prompt = "Push to GitHub and create PR? (yes/no): "
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

        # Allow empty input for name confirmation and URL states (Enter = skip)
        allows_empty = (
            flow.is_awaiting_name_confirmation()
            or flow.is_awaiting_csv_url()
            or flow.is_awaiting_dcat_url()
        )
        if not user_input and not allows_empty:
            continue

        try:
            state = flow.continue_with_input(user_input)
        except RequestLimitReached as e:
            # AI request limit reached - ask user if they want to continue
            print(f"\n⚠ Warning: {e}", file=sys.stderr)
            print(f"\nYou have made {e.current_count} AI requests (limit: {e.limit}).")
            print("This limit helps prevent runaway costs from too many API calls.")

            response = input("\nContinue with more requests? (yes/no): ").strip().lower()
            if response in ('yes', 'y'):
                # Reset counter and retry
                flow.reset_request_count()
                print(f"✓ Request counter reset. Continuing...")
                # Retry the same input
                try:
                    state = flow.continue_with_input(user_input)
                except Exception as retry_error:
                    logger.exception("Error processing input after reset")
                    print(f"\nError: {retry_error}", file=sys.stderr)
                    continue
            else:
                print("\nExiting at user request.")
                return 0
        except Exception as e:
            logger.exception("Error processing input")
            print(f"\nError: {e}", file=sys.stderr)
            continue

        # Show token usage stats
        print(f"\n[{format_token_stats(flow)}]")

        # Check for errors
        if state.current_state == FlowState.ERROR:
            print(f"\nError: {state.error_message}", file=sys.stderr)
            return 1

        # Show PR preview when transitioning to PREVIEW state
        if flow.is_awaiting_pr_confirmation() and flow.get_pr_description():
            print("\n" + "=" * 60)
            print("PR Preview:")
            print("-" * 60)
            print(flow.get_pr_description())
            print("=" * 60)
            continue

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

    # Show final token usage summary
    print("\n" + "=" * 60)
    print("Session Summary")
    print("=" * 60)
    ai = flow.ai_service
    usage = ai.token_usage
    cost = ai.get_total_cost()

    print(f"Total Requests: {ai.request_count}")
    print(f"Total Tokens: {usage.total_tokens:,}")
    print(f"  - Input: {usage.input_tokens:,}")
    print(f"  - Output: {usage.output_tokens:,}")
    if usage.cached_tokens > 0:
        print(f"  - Cached: {usage.cached_tokens:,}")
    print(f"\nEstimated Cost: CHF {cost:.4f}")
    print(f"  (Input: CHF {(usage.input_tokens / 1_000_000) * flow._config.azure.price_per_1m_input_tokens:.4f}, "
          f"Output: CHF {(usage.output_tokens / 1_000_000) * flow._config.azure.price_per_1m_output_tokens:.4f}")
    if usage.cached_tokens > 0:
        print(f"   Cached: CHF {(usage.cached_tokens / 1_000_000) * flow._config.azure.price_per_1m_cached_tokens:.4f})")
    else:
        print(")")

    return 0


if __name__ == "__main__":
    sys.exit(main())
