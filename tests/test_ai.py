"""Tests for AI service integration."""

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from openai import APIConnectionError, APIStatusError, RateLimitError

from ogd_to_lod.ai import (
    AIService,
    AIServiceError,
    CodeBlock,
    ConnectionFailed,
    Message,
    ParsedResponse,
    RateLimitExceeded,
    DEFAULT_SYSTEM_PROMPT,
)
from ogd_to_lod.config import AzureOpenAIConfig


@pytest.fixture
def azure_config():
    """Create a test Azure OpenAI configuration."""
    return AzureOpenAIConfig(
        endpoint="https://test.openai.azure.com/",
        api_key="test-api-key",
        deployment="gpt-4",
        api_version="2024-02-15-preview",
    )


@pytest.fixture
def mock_client():
    """Create a mock AzureChatOpenAI client."""
    with patch("ogd_to_lod.ai.service.AzureChatOpenAI") as mock:
        mock_instance = MagicMock()
        mock.return_value = mock_instance
        yield mock_instance


class TestAIServiceInitialization:
    """Tests for AIService initialization."""

    def test_init_with_default_prompt(self, azure_config, mock_client):
        """Test initialization with default system prompt."""
        service = AIService(azure_config)
        assert service.system_prompt == DEFAULT_SYSTEM_PROMPT

    def test_init_with_custom_prompt(self, azure_config, mock_client):
        """Test initialization with custom system prompt string."""
        custom_prompt = "You are a helpful assistant."
        service = AIService(azure_config, system_prompt=custom_prompt)
        assert service.system_prompt == custom_prompt

    def test_init_with_prompt_file(self, azure_config, mock_client, tmp_path):
        """Test initialization with system prompt from file."""
        prompt_content = "Custom prompt from file."
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text(prompt_content)

        service = AIService(azure_config, system_prompt_file=prompt_file)
        assert service.system_prompt == prompt_content

    def test_init_with_prompt_file_takes_precedence(self, azure_config, mock_client, tmp_path):
        """Test that prompt file takes precedence over prompt string."""
        prompt_string = "This should be ignored."
        file_content = "This should be used."
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text(file_content)

        service = AIService(
            azure_config,
            system_prompt=prompt_string,
            system_prompt_file=prompt_file,
        )
        assert service.system_prompt == file_content

    def test_init_with_missing_prompt_file(self, azure_config, mock_client):
        """Test that missing prompt file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="System prompt file not found"):
            AIService(azure_config, system_prompt_file="/nonexistent/path.txt")

    def test_system_prompt_setter(self, azure_config, mock_client):
        """Test setting system prompt after initialization."""
        service = AIService(azure_config)
        new_prompt = "New system prompt."
        service.system_prompt = new_prompt
        assert service.system_prompt == new_prompt


class TestConversationHistory:
    """Tests for conversation history management."""

    def test_initial_history_is_empty(self, azure_config, mock_client):
        """Test that conversation history starts empty."""
        service = AIService(azure_config)
        assert service.conversation_history == []

    def test_history_returns_copy(self, azure_config, mock_client):
        """Test that conversation_history returns a copy."""
        service = AIService(azure_config)
        history = service.conversation_history
        history.append(Message(role="user", content="test"))
        assert service.conversation_history == []  # Original not modified

    def test_clear_history(self, azure_config, mock_client):
        """Test clearing conversation history."""
        service = AIService(azure_config)
        mock_client.invoke.return_value = AIMessage(content="Response")

        service.send_message("Hello")
        assert len(service.conversation_history) == 2

        service.clear_history()
        assert service.conversation_history == []

    def test_add_context(self, azure_config, mock_client):
        """Test adding context to conversation."""
        service = AIService(azure_config)
        context = "CSV columns: name, age, city"
        service.add_context(context)

        assert len(service.conversation_history) == 1
        assert service.conversation_history[0].role == "system"
        assert service.conversation_history[0].content == context

    def test_history_maintained_across_turns(self, azure_config, mock_client):
        """Test that history is maintained across multiple turns."""
        mock_client.invoke.side_effect = [
            AIMessage(content="Response 1"),
            AIMessage(content="Response 2"),
        ]

        service = AIService(azure_config)
        service.send_message("Message 1")
        service.send_message("Message 2")

        history = service.conversation_history
        assert len(history) == 4
        assert history[0] == Message(role="user", content="Message 1")
        assert history[1] == Message(role="assistant", content="Response 1")
        assert history[2] == Message(role="user", content="Message 2")
        assert history[3] == Message(role="assistant", content="Response 2")


class TestSendMessage:
    """Tests for sending messages to the AI."""

    def test_send_message_success(self, azure_config, mock_client):
        """Test successful message sending."""
        expected_response = "This is the AI response."
        mock_client.invoke.return_value = AIMessage(content=expected_response)

        service = AIService(azure_config)
        response = service.send_message("Hello, AI!")

        assert response == expected_response
        mock_client.invoke.assert_called_once()

    def test_send_message_includes_system_prompt(self, azure_config, mock_client):
        """Test that system prompt is included in messages."""
        mock_client.invoke.return_value = AIMessage(content="Response")

        service = AIService(azure_config, system_prompt="Custom prompt")
        service.send_message("Hello")

        call_args = mock_client.invoke.call_args[0][0]
        assert isinstance(call_args[0], SystemMessage)
        assert call_args[0].content == "Custom prompt"

    def test_send_message_includes_history(self, azure_config, mock_client):
        """Test that conversation history is included in messages."""
        mock_client.invoke.side_effect = [
            AIMessage(content="First response"),
            AIMessage(content="Second response"),
        ]

        service = AIService(azure_config)
        service.send_message("First message")
        service.send_message("Second message")

        # Check second call includes history
        call_args = mock_client.invoke.call_args[0][0]
        # Should have: system + user1 + assistant1 + user2
        assert len(call_args) == 4
        assert isinstance(call_args[1], HumanMessage)
        assert call_args[1].content == "First message"
        assert isinstance(call_args[2], AIMessage)
        assert call_args[2].content == "First response"


class TestErrorHandling:
    """Tests for API error handling."""

    def test_rate_limit_retry_success(self, azure_config, mock_client):
        """Test that rate limit errors trigger retry and succeed."""
        # First call raises rate limit, second succeeds
        mock_client.invoke.side_effect = [
            RateLimitError(
                message="Rate limit exceeded",
                response=MagicMock(status_code=429),
                body=None,
            ),
            AIMessage(content="Success after retry"),
        ]

        service = AIService(azure_config, max_retries=3, retry_delay=0.01)
        response = service.send_message("Hello")

        assert response == "Success after retry"
        assert mock_client.invoke.call_count == 2

    def test_rate_limit_exhausted(self, azure_config, mock_client):
        """Test that exhausted retries raise RateLimitExceeded."""
        mock_client.invoke.side_effect = RateLimitError(
            message="Rate limit exceeded",
            response=MagicMock(status_code=429),
            body=None,
        )

        service = AIService(azure_config, max_retries=2, retry_delay=0.01)

        with pytest.raises(RateLimitExceeded, match="Rate limit exceeded"):
            service.send_message("Hello")

        # User message should be removed from history
        assert service.conversation_history == []

    def test_connection_error(self, azure_config, mock_client):
        """Test that connection errors raise ConnectionFailed."""
        mock_client.invoke.side_effect = APIConnectionError(
            request=MagicMock(),
        )

        service = AIService(azure_config)

        with pytest.raises(ConnectionFailed, match="Failed to connect"):
            service.send_message("Hello")

        # User message should be removed from history
        assert service.conversation_history == []

    def test_api_status_error(self, azure_config, mock_client):
        """Test that API status errors raise AIServiceError."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_client.invoke.side_effect = APIStatusError(
            message="Internal server error",
            response=mock_response,
            body=None,
        )

        service = AIService(azure_config)

        with pytest.raises(AIServiceError, match="API error"):
            service.send_message("Hello")

        # User message should be removed from history
        assert service.conversation_history == []

    def test_unexpected_error(self, azure_config, mock_client):
        """Test that unexpected errors raise AIServiceError."""
        mock_client.invoke.side_effect = ValueError("Unexpected error")

        service = AIService(azure_config)

        with pytest.raises(AIServiceError, match="Unexpected error"):
            service.send_message("Hello")


class TestParseResponse:
    """Tests for response parsing."""

    def test_parse_text_only(self):
        """Test parsing response with no code blocks."""
        response = "This is a simple text response."
        parsed = AIService.parse_response(response)

        assert parsed.text == "This is a simple text response."
        assert parsed.code_blocks == []

    def test_parse_single_code_block(self):
        """Test parsing response with single code block."""
        response = """Here is some YAML:

```yaml
dimensions:
  - column: year
    type: temporal
```

That's the mapping."""

        parsed = AIService.parse_response(response)

        assert "Here is some YAML:" in parsed.text
        assert "That's the mapping." in parsed.text
        assert len(parsed.code_blocks) == 1
        assert parsed.code_blocks[0].language == "yaml"
        assert "dimensions:" in parsed.code_blocks[0].content

    def test_parse_multiple_code_blocks(self):
        """Test parsing response with multiple code blocks."""
        response = """First, the mapping:

```yaml
dimensions:
  - column: year
```

And the RML:

```turtle
@prefix rml: <http://semweb.mmlab.be/ns/rml#> .
```

Done."""

        parsed = AIService.parse_response(response)

        assert len(parsed.code_blocks) == 2
        assert parsed.code_blocks[0].language == "yaml"
        assert parsed.code_blocks[1].language == "turtle"

    def test_parse_code_block_no_language(self):
        """Test parsing code block without language specifier."""
        response = """Some code:

```
plain code block
```

Done."""

        parsed = AIService.parse_response(response)

        assert len(parsed.code_blocks) == 1
        assert parsed.code_blocks[0].language == ""
        assert parsed.code_blocks[0].content == "plain code block"

    def test_get_yaml_blocks(self):
        """Test extracting only YAML blocks."""
        response = """```yaml
yaml1: value1
```

```turtle
@prefix ex: <http://example.org/> .
```

```yaml
yaml2: value2
```"""

        parsed = AIService.parse_response(response)
        yaml_blocks = parsed.get_yaml_blocks()

        assert len(yaml_blocks) == 2
        assert "yaml1: value1" in yaml_blocks[0]
        assert "yaml2: value2" in yaml_blocks[1]

    def test_get_turtle_blocks(self):
        """Test extracting only Turtle blocks."""
        response = """```yaml
data: value
```

```turtle
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
```

```turtle
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
```"""

        parsed = AIService.parse_response(response)
        turtle_blocks = parsed.get_turtle_blocks()

        assert len(turtle_blocks) == 2
        assert "@prefix rdf:" in turtle_blocks[0]
        assert "@prefix rdfs:" in turtle_blocks[1]

    def test_parse_multiline_code_block(self):
        """Test parsing multiline code blocks."""
        response = """```yaml
dimensions:
  - column: year
    type: temporal
    granularity: year
  - column: region
    type: spatial
    hierarchy: district
measures:
  - column: count
    unit: persons
```"""

        parsed = AIService.parse_response(response)

        assert len(parsed.code_blocks) == 1
        content = parsed.code_blocks[0].content
        assert "dimensions:" in content
        assert "measures:" in content
        assert "column: count" in content

    def test_parse_code_block_no_newline_after_language(self):
        """Test parsing code block without newline after language tag."""
        response = """```yaml dimensions:
  - column: year
```"""
        parsed = AIService.parse_response(response)
        assert len(parsed.code_blocks) == 1
        assert parsed.code_blocks[0].language == "yaml"
        assert "dimensions:" in parsed.code_blocks[0].content

    def test_parse_code_block_space_before_language(self):
        """Test parsing code block with space before language tag."""
        response = """``` yaml
dimensions:
  - column: year
```"""
        parsed = AIService.parse_response(response)
        assert len(parsed.code_blocks) == 1
        assert parsed.code_blocks[0].language == "yaml"

    def test_parse_code_block_uppercase_language(self):
        """Test parsing code block with uppercase language tag."""
        response = """```YAML
dimensions:
  - column: year
```"""
        parsed = AIService.parse_response(response)
        assert len(parsed.code_blocks) == 1
        assert parsed.code_blocks[0].language == "yaml"

    def test_parse_code_block_mixed_case_language(self):
        """Test parsing code block with mixed case language tag."""
        response = """```Yaml
dimensions:
  - column: year
```"""
        parsed = AIService.parse_response(response)
        assert len(parsed.code_blocks) == 1
        assert parsed.code_blocks[0].language == "yaml"

    def test_parse_code_block_with_tabs(self):
        """Test parsing code block with tab after backticks."""
        response = """```\tyaml
dimensions:
  - column: year
```"""
        parsed = AIService.parse_response(response)
        assert len(parsed.code_blocks) == 1
        assert parsed.code_blocks[0].language == "yaml"


class TestParsedResponseDataclass:
    """Tests for ParsedResponse dataclass."""

    def test_default_code_blocks(self):
        """Test that code_blocks defaults to empty list."""
        parsed = ParsedResponse(text="Hello")
        assert parsed.code_blocks == []

    def test_code_blocks_preserved(self):
        """Test that code_blocks are preserved."""
        blocks = [CodeBlock(language="yaml", content="test")]
        parsed = ParsedResponse(text="Hello", code_blocks=blocks)
        assert parsed.code_blocks == blocks


class TestCodeBlockDataclass:
    """Tests for CodeBlock dataclass."""

    def test_code_block_creation(self):
        """Test creating a CodeBlock."""
        block = CodeBlock(language="yaml", content="key: value")
        assert block.language == "yaml"
        assert block.content == "key: value"


class TestMessageDataclass:
    """Tests for Message dataclass."""

    def test_message_creation(self):
        """Test creating a Message."""
        msg = Message(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"


class TestDefaultSystemPrompt:
    """Tests for the default system prompt."""

    def test_default_prompt_mentions_rml(self):
        """Test that default prompt mentions RML."""
        assert "RML" in DEFAULT_SYSTEM_PROMPT

    def test_default_prompt_mentions_cubelink(self):
        """Test that default prompt mentions cube.link vocabulary."""
        assert "cube.link" in DEFAULT_SYSTEM_PROMPT

    def test_default_prompt_mentions_response_format(self):
        """Test that default prompt specifies response format."""
        assert "YAML" in DEFAULT_SYSTEM_PROMPT
        assert "YARRRML" in DEFAULT_SYSTEM_PROMPT
