"""Azure OpenAI service wrapper with conversation management."""

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI
from openai import APIConnectionError, APIStatusError, RateLimitError

from ogd_to_lod.config import AzureOpenAIConfig

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
- Put RML output in fenced Turtle code blocks"""


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

        # Initialize the LangChain Azure OpenAI client
        self._client = AzureChatOpenAI(
            azure_endpoint=config.endpoint,
            azure_deployment=config.deployment,
            api_key=config.api_key,
            api_version=config.api_version,
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

    def send_message(self, message: str) -> str:
        """Send a message to the AI and get a response.

        The message and response are added to conversation history.

        Args:
            message: User message to send.

        Returns:
            AI response content.

        Raises:
            RateLimitExceeded: If rate limit is exceeded after all retries.
            ConnectionFailed: If connection to API fails.
            AIServiceError: For other API errors.
        """
        messages = self._build_messages(message)

        # Add user message to history
        self._conversation_history.append(Message(role="user", content=message))

        # Attempt with retries
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                response = self._client.invoke(messages)
                response_content = str(response.content)

                # Add assistant response to history
                self._conversation_history.append(
                    Message(role="assistant", content=response_content)
                )

                return response_content

            except RateLimitError as e:
                last_error = e
                if attempt < self._max_retries - 1:
                    delay = self._retry_delay * (2**attempt)
                    time.sleep(delay)
                continue

            except APIConnectionError as e:
                # Remove user message from history on connection failure
                self._conversation_history.pop()
                raise ConnectionFailed(
                    f"Failed to connect to Azure OpenAI: {e}"
                ) from e

            except APIStatusError as e:
                # Remove user message from history on error
                self._conversation_history.pop()
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
