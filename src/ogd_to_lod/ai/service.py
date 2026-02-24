"""Azure OpenAI service wrapper with conversation management."""

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI
from openai import APIConnectionError, APIStatusError, RateLimitError

from ogd_to_lod.config import AzureOpenAIConfig
from ogd_to_lod.logging import get_logger

logger = get_logger(__name__)

# Type for token usage callback
TokenCallback = Callable[[int, "TokenUsage", "TokenUsage", float], None]

# Default system prompt for RML mapping assistance
DEFAULT_SYSTEM_PROMPT = """\
You are an RDF mapping expert specializing in creating RML (RDF Mapping Language) \
configurations for statistical data cubes.

Your task: Help users transform CSV files into RDF data cubes using:
- cube.link vocabulary for cube structure
- schema.org vocabulary for dimensions (DefinedTerm, DefinedTermSet, isPartOf)

Guidelines:
- Identify dimensions vs measures from column analysis
- Suggest appropriate dimension types (temporal, spatial, categorical)
- Propose hierarchies where applicable
- Ask clarifying questions when column purpose is ambiguous
- Explain your reasoning when making suggestions
- When user overrides a suggestion, accept it and adjust accordingly

Response format:
- Use markdown for explanations
- Put structured data (mapping proposals) in fenced YAML code blocks
- Put YARRRML mappings in fenced YAML code blocks"""


@dataclass
class TokenUsage:
    """Token usage statistics for AI requests."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0

    def add(self, other: "TokenUsage") -> None:
        """Add another TokenUsage to this one.

        Args:
            other: TokenUsage to add.
        """
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cached_tokens += other.cached_tokens
        self.total_tokens += other.total_tokens

    def calculate_cost(
        self,
        price_per_1m_input: float,
        price_per_1m_output: float,
        price_per_1m_cached: float,
    ) -> float:
        """Calculate cost in CHF based on token usage and pricing.

        Args:
            price_per_1m_input: Price per 1M input tokens.
            price_per_1m_output: Price per 1M output tokens.
            price_per_1m_cached: Price per 1M cached tokens.

        Returns:
            Total cost in CHF.
        """
        cost = 0.0
        cost += (self.input_tokens / 1_000_000) * price_per_1m_input
        cost += (self.output_tokens / 1_000_000) * price_per_1m_output
        cost += (self.cached_tokens / 1_000_000) * price_per_1m_cached
        return cost


@dataclass
class CodeBlock:
    """Represents an extracted code block from AI response."""

    language: str
    content: str


@dataclass
class ParsedResponse:
    """Parsed AI response with separated text and code blocks."""

    text: str
    code_blocks: list[CodeBlock] = field(default_factory=list)

    def get_yaml_blocks(self) -> list[str]:
        """Get all YAML code block contents."""
        return [block.content for block in self.code_blocks if block.language == "yaml"]

    def get_turtle_blocks(self) -> list[str]:
        """Get all Turtle code block contents."""
        return [block.content for block in self.code_blocks if block.language == "turtle"]


@dataclass
class Message:
    """A conversation message."""

    role: str  # "user", "assistant", or "system"
    content: str


class AIServiceError(Exception):
    """Base exception for AI service errors."""

    pass


class RateLimitExceeded(AIServiceError):
    """Rate limit exceeded error."""

    pass


class RequestLimitReached(AIServiceError):
    """Request limit reached - user confirmation needed to continue."""

    def __init__(self, current_count: int, limit: int):
        """Initialize with request counts.

        Args:
            current_count: Current number of requests made.
            limit: Configured request limit.
        """
        self.current_count = current_count
        self.limit = limit
        super().__init__(
            f"Request limit reached: {current_count}/{limit} requests made. "
            "User confirmation required to continue."
        )


class ConnectionFailed(AIServiceError):
    """Connection to API failed."""

    pass


class AIService:
    """Azure OpenAI service wrapper with conversation management.

    Provides:
    - Message sending to Azure OpenAI
    - System prompt configuration (from string or file)
    - Conversation history management
    - Response parsing for code blocks
    - Error handling with retry logic
    """

    def __init__(
        self,
        config: AzureOpenAIConfig,
        system_prompt: str | None = None,
        system_prompt_file: str | Path | None = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ):
        """Initialize the AI service.

        Args:
            config: Azure OpenAI configuration.
            system_prompt: System prompt string. If None, uses default.
            system_prompt_file: Path to file containing system prompt.
                Takes precedence over system_prompt if provided.
            max_retries: Maximum number of retries on rate limit errors.
            retry_delay: Initial delay between retries (exponential backoff).
        """
        self._config = config
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._conversation_history: list[Message] = []
        self._request_count = 0  # Track number of requests made
        self._request_limit = config.max_requests  # Maximum requests before asking user
        self._token_usage = TokenUsage()  # Cumulative token usage
        self._last_request_tokens = TokenUsage()  # Tokens from last request
        self._token_callbacks: list[TokenCallback] = []  # Callbacks for token updates

        # Initialize the LangChain Azure OpenAI client
        # Disable SDK retries - we handle retries ourselves for better user feedback
        self._client = AzureChatOpenAI(
            azure_endpoint=config.endpoint,
            azure_deployment=config.deployment,
            api_key=config.api_key,
            api_version=config.api_version,
            max_retries=0,  # Disable automatic retries - we handle them ourselves
        )

        # Load system prompt
        self._system_prompt = self._load_system_prompt(system_prompt, system_prompt_file)

    def _load_system_prompt(
        self,
        prompt_string: str | None,
        prompt_file: str | Path | None,
    ) -> str:
        """Load system prompt from file or string.

        Args:
            prompt_string: Direct prompt string.
            prompt_file: Path to prompt file.

        Returns:
            Loaded system prompt string.

        Raises:
            FileNotFoundError: If prompt file does not exist.
        """
        if prompt_file is not None:
            path = Path(prompt_file)
            if not path.exists():
                raise FileNotFoundError(f"System prompt file not found: {path}")
            return path.read_text().strip()

        if prompt_string is not None:
            return prompt_string

        return DEFAULT_SYSTEM_PROMPT

    @property
    def system_prompt(self) -> str:
        """Get the current system prompt."""
        return self._system_prompt

    @system_prompt.setter
    def system_prompt(self, value: str) -> None:
        """Set a new system prompt."""
        self._system_prompt = value

    @property
    def conversation_history(self) -> list[Message]:
        """Get a copy of the conversation history."""
        return list(self._conversation_history)

    def clear_history(self) -> None:
        """Clear the conversation history."""
        self._conversation_history.clear()

    @property
    def request_count(self) -> int:
        """Get the current request count."""
        return self._request_count

    @property
    def request_limit(self) -> int:
        """Get the configured request limit."""
        return self._request_limit

    def reset_request_count(self) -> None:
        """Reset the request counter to zero."""
        self._request_count = 0

    @property
    def token_usage(self) -> TokenUsage:
        """Get cumulative token usage."""
        return self._token_usage

    @property
    def last_request_tokens(self) -> TokenUsage:
        """Get token usage from the last request."""
        return self._last_request_tokens

    def get_total_cost(self) -> float:
        """Calculate total cost in CHF based on token usage.

        Returns:
            Total cost in CHF.
        """
        return self._token_usage.calculate_cost(
            self._config.price_per_1m_input_tokens,
            self._config.price_per_1m_output_tokens,
            self._config.price_per_1m_cached_tokens,
        )

    def register_token_callback(self, callback: TokenCallback) -> None:
        """Register a callback to be called after each request.

        The callback receives: (request_count, last_tokens, total_tokens, total_cost)

        Args:
            callback: Function to call after each token update.
        """
        self._token_callbacks.append(callback)

    def unregister_token_callback(self, callback: TokenCallback) -> None:
        """Unregister a token callback.

        Args:
            callback: Function to remove from callbacks.
        """
        if callback in self._token_callbacks:
            self._token_callbacks.remove(callback)

    def add_context(self, context: str) -> None:
        """Add context to the conversation as a system message.

        This is useful for providing CSV schema, DCAT metadata, etc.

        Args:
            context: Context information to add.
        """
        self._conversation_history.append(Message(role="system", content=context))

    def _build_messages(self, user_message: str) -> list[Any]:
        """Build the message list for the API call.

        Args:
            user_message: The user's message.

        Returns:
            List of LangChain message objects.
        """
        messages = [SystemMessage(content=self._system_prompt)]

        # Add conversation history
        for msg in self._conversation_history:
            if msg.role == "user":
                messages.append(HumanMessage(content=msg.content))
            elif msg.role == "assistant":
                messages.append(AIMessage(content=msg.content))
            elif msg.role == "system":
                messages.append(SystemMessage(content=msg.content))

        # Add current user message
        messages.append(HumanMessage(content=user_message))

        return messages

    @staticmethod
    def _extract_token_usage(response: Any) -> TokenUsage:
        """Extract token usage from API response.

        Args:
            response: LangChain AIMessage response.

        Returns:
            TokenUsage with extracted token counts.
        """
        usage = TokenUsage()

        # Try to extract from response_metadata (LangChain format)
        if hasattr(response, "response_metadata"):
            metadata = response.response_metadata
            if "token_usage" in metadata:
                token_data = metadata["token_usage"]
                usage.input_tokens = token_data.get("prompt_tokens", 0)
                usage.output_tokens = token_data.get("completion_tokens", 0)
                usage.total_tokens = token_data.get("total_tokens", 0)

                # Azure OpenAI may provide cached tokens separately
                if "prompt_tokens_details" in token_data:
                    details = token_data["prompt_tokens_details"]
                    usage.cached_tokens = details.get("cached_tokens", 0)

        # Try to extract from usage_metadata (newer LangChain format)
        elif hasattr(response, "usage_metadata"):
            metadata = response.usage_metadata
            usage.input_tokens = metadata.get("input_tokens", 0)
            usage.output_tokens = metadata.get("output_tokens", 0)
            usage.total_tokens = metadata.get("total_tokens", 0)

        return usage

    def send_message(self, message: str) -> str:
        """Send a message to the AI and get a response.

        The message and response are added to conversation history.

        Args:
            message: User message to send.

        Returns:
            AI response content.

        Raises:
            RequestLimitReached: If request limit is reached.
            RateLimitExceeded: If rate limit is exceeded after all retries.
            ConnectionFailed: If connection to API fails.
            AIServiceError: For other API errors.
        """
        # Check if request limit reached
        if self._request_count >= self._request_limit:
            raise RequestLimitReached(self._request_count, self._request_limit)

        # Log that we're making a request
        logger.debug(
            f"Sending AI request #{self._request_count + 1} "
            f"(message length: {len(message)} chars)"
        )

        messages = self._build_messages(message)

        # Add user message to history
        self._conversation_history.append(Message(role="user", content=message))

        # Attempt with retries
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                response = self._client.invoke(messages)
                response_content = str(response.content)

                # Increment request counter on successful request
                self._request_count += 1

                # Extract and track token usage
                usage = self._extract_token_usage(response)
                self._last_request_tokens = usage
                self._token_usage.add(usage)

                # Log token usage
                logger.debug(
                    f"Request #{self._request_count}: "
                    f"{usage.input_tokens} input, {usage.output_tokens} output, "
                    f"{usage.cached_tokens} cached tokens"
                )

                # Notify callbacks about token update
                total_cost = self.get_total_cost()
                for callback in self._token_callbacks:
                    try:
                        callback(
                            self._request_count,
                            self._last_request_tokens,
                            self._token_usage,
                            total_cost,
                        )
                    except Exception as e:
                        logger.warning(f"Token callback failed: {e}")

                # Add assistant response to history
                self._conversation_history.append(
                    Message(role="assistant", content=response_content)
                )

                return response_content

            except RateLimitError as e:
                last_error = e
                if attempt < self._max_retries - 1:
                    delay = self._retry_delay * (2**attempt)
                    logger.warning(
                        f"Rate limit hit (429). Retrying in {delay}s... "
                        f"(attempt {attempt + 1}/{self._max_retries})"
                    )
                    # Notify user via print (visible in console)
                    print(
                        f"  ⚠ Rate limit reached. Waiting {delay:.1f}s before retry "
                        f"({attempt + 1}/{self._max_retries})...",
                        flush=True,
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"Rate limit exceeded after {self._max_retries} attempts"
                    )
                continue

            except APIConnectionError as e:
                # Remove user message from history on connection failure
                self._conversation_history.pop()
                logger.error(f"Connection to Azure OpenAI failed: {e}")
                print(
                    f"\n⚠ Connection failed: {e}\n"
                    f"Please check your network and Azure OpenAI endpoint configuration.",
                    flush=True,
                )
                raise ConnectionFailed(
                    f"Failed to connect to Azure OpenAI: {e}"
                ) from e

            except APIStatusError as e:
                # Remove user message from history on error
                self._conversation_history.pop()
                logger.error(f"Azure OpenAI API error {e.status_code}: {e.message}")

                # Provide user-friendly error messages
                if e.status_code == 401:
                    print(
                        f"\n⚠ Authentication failed (401)\n"
                        f"Please check your AZURE_OPENAI_KEY environment variable.",
                        flush=True,
                    )
                elif e.status_code == 404:
                    print(
                        f"\n⚠ Deployment not found (404)\n"
                        f"Please check your deployment name in config.yaml.",
                        flush=True,
                    )
                elif e.status_code == 429:
                    print(
                        f"\n⚠ Rate limit exceeded (429)\n"
                        f"Your Azure OpenAI quota has been exceeded.",
                        flush=True,
                    )
                else:
                    print(
                        f"\n⚠ API error ({e.status_code}): {e.message}",
                        flush=True,
                    )

                raise AIServiceError(
                    f"Azure OpenAI API error ({e.status_code}): {e.message}"
                ) from e

            except Exception as e:
                # Remove user message from history on unexpected error
                self._conversation_history.pop()
                raise AIServiceError(f"Unexpected error: {e}") from e

        # All retries exhausted for rate limit
        self._conversation_history.pop()
        raise RateLimitExceeded(
            f"Rate limit exceeded after {self._max_retries} retries: {last_error}"
        )

    @staticmethod
    def parse_response(response: str) -> ParsedResponse:
        """Parse an AI response to extract code blocks.

        Extracts fenced code blocks (```language ... ```) and returns
        both the plain text and the extracted code blocks.

        Handles various formats:
        - ```yaml\\ncontent``` (standard)
        - ```yaml content``` (no newline after language)
        - ``` yaml\\ncontent``` (space before language)
        - ```YAML\\ncontent``` (uppercase language)
        - ```\\ncontent``` (no language)

        Args:
            response: Raw AI response text.

        Returns:
            ParsedResponse with text and code blocks separated.
        """
        # Pattern to match fenced code blocks with flexible formatting
        # Handles: ```lang\n, ``` lang\n, ```lang (no newline), ```\n
        code_block_pattern = r"```[ \t]*(\w+)?[ \t]*\n?(.*?)```"

        code_blocks: list[CodeBlock] = []

        def replace_block(match: re.Match) -> str:
            language = (match.group(1) or "").lower().strip()
            content = match.group(2).strip()
            code_blocks.append(CodeBlock(language=language, content=content))
            return ""

        # Extract code blocks and get remaining text
        text = re.sub(code_block_pattern, replace_block, response, flags=re.DOTALL)

        # Clean up the text (remove extra blank lines)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

        return ParsedResponse(text=text, code_blocks=code_blocks)
