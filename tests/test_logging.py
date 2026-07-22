"""Tests for logging and tracing utilities."""

import logging
import re
from io import StringIO
from unittest.mock import patch

import pytest

from ogd_to_lod.logging import (
    DEFAULT_TRUNCATE_LENGTH,
    StructuredFormatter,
    get_log_level,
    get_logger,
    get_session_id,
    log_ai_call,
    log_ai_response,
    log_state_transition,
    new_session,
    set_session_id,
    setup_logging,
    truncate_text,
    with_state_logging,
)


class TestSessionId:
    """Tests for session ID management."""

    def test_new_session_generates_uuid(self):
        """Test that new_session generates a valid session ID."""
        session_id = new_session()
        assert session_id is not None
        assert len(session_id) == 8  # Short UUID format

    def test_set_session_id_custom(self):
        """Test setting a custom session ID."""
        custom_id = "test-123"
        result = set_session_id(custom_id)
        assert result == custom_id
        assert get_session_id() == custom_id

    def test_set_session_id_none_generates_uuid(self):
        """Test that passing None generates a new UUID."""
        result = set_session_id(None)
        assert result is not None
        assert len(result) == 8
        assert get_session_id() == result

    def test_get_session_id_returns_current(self):
        """Test that get_session_id returns the current session."""
        set_session_id("my-session")
        assert get_session_id() == "my-session"


class TestStructuredFormatter:
    """Tests for the StructuredFormatter."""

    def test_format_includes_timestamp(self):
        """Test that formatted output includes ISO timestamp."""
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        result = formatter.format(record)

        # Check for ISO 8601 timestamp pattern
        timestamp_pattern = r"\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\]"
        assert re.search(timestamp_pattern, result) is not None

    def test_format_includes_level(self):
        """Test that formatted output includes log level."""
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test_logger",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="Warning message",
            args=(),
            exc_info=None,
        )

        result = formatter.format(record)
        assert "[WARNING]" in result

    def test_format_includes_session_id(self):
        """Test that formatted output includes session ID."""
        set_session_id("test-session-456")
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        result = formatter.format(record)
        assert "[test-session-456]" in result

    def test_format_includes_extra_fields(self):
        """Test that formatted output includes extra fields."""
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        record.extra_fields = {"key1": "value1", "key2": "value2"}

        result = formatter.format(record)
        assert "key1=value1" in result
        assert "key2=value2" in result


class TestGetLogLevel:
    """Tests for get_log_level function."""

    def test_default_is_info(self, monkeypatch):
        """Test that default log level is INFO."""
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        assert get_log_level() == logging.INFO

    def test_debug_level(self, monkeypatch):
        """Test DEBUG log level."""
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        assert get_log_level() == logging.DEBUG

    def test_warning_level(self, monkeypatch):
        """Test WARNING log level."""
        monkeypatch.setenv("LOG_LEVEL", "WARNING")
        assert get_log_level() == logging.WARNING

    def test_error_level(self, monkeypatch):
        """Test ERROR log level."""
        monkeypatch.setenv("LOG_LEVEL", "ERROR")
        assert get_log_level() == logging.ERROR

    def test_case_insensitive(self, monkeypatch):
        """Test that log level is case insensitive."""
        monkeypatch.setenv("LOG_LEVEL", "debug")
        assert get_log_level() == logging.DEBUG

    def test_invalid_level_defaults_to_info(self, monkeypatch):
        """Test that invalid log level defaults to INFO."""
        monkeypatch.setenv("LOG_LEVEL", "INVALID")
        assert get_log_level() == logging.INFO


class TestSetupLogging:
    """Tests for setup_logging function."""

    def test_returns_session_logger(self, monkeypatch):
        """Test that setup_logging returns a SessionLogger."""
        monkeypatch.setenv("LOG_LEVEL", "INFO")
        # Use unique logger name to avoid conflicts
        logger = setup_logging("test_setup_logger_1")
        assert logger is not None

    def test_logger_has_handler(self, monkeypatch):
        """Test that the logger has a handler configured."""
        monkeypatch.setenv("LOG_LEVEL", "INFO")
        logger = setup_logging("test_setup_logger_2")
        # Access underlying logger
        assert len(logger.logger.handlers) > 0

    def test_get_logger_returns_configured_logger(self, monkeypatch):
        """Test that get_logger returns a configured logger."""
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        logger = get_logger("test_get_logger")
        assert logger is not None


class TestTruncateText:
    """Tests for truncate_text function."""

    def test_short_text_unchanged(self):
        """Test that short text is not truncated."""
        text = "Short text"
        result = truncate_text(text, 100)
        assert result == text

    def test_exact_length_unchanged(self):
        """Test that text at exact max length is not truncated."""
        text = "A" * 100
        result = truncate_text(text, 100)
        assert result == text

    def test_long_text_truncated(self):
        """Test that long text is truncated with ellipsis."""
        text = "A" * 200
        result = truncate_text(text, 100)
        assert result.startswith("A" * 100)
        assert "..." in result
        assert "200 chars total" in result

    def test_default_truncate_length(self):
        """Test truncation with default length."""
        text = "A" * (DEFAULT_TRUNCATE_LENGTH + 100)
        result = truncate_text(text)
        assert len(result.split("...")[0]) == DEFAULT_TRUNCATE_LENGTH


class TestLogStateTransition:
    """Tests for log_state_transition function."""

    def test_logs_transition(self, monkeypatch):
        """Test that state transition is logged correctly."""
        monkeypatch.setenv("LOG_LEVEL", "INFO")
        new_session()

        # Capture log output
        logger = get_logger("test_state_transition")
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(StructuredFormatter())
        logger.logger.addHandler(handler)

        log_state_transition(logger, "start", "processing")

        output = stream.getvalue()
        assert "start" in output
        assert "processing" in output
        assert "State transition" in output

    def test_logs_metadata(self, monkeypatch):
        """Test that transition metadata is logged."""
        monkeypatch.setenv("LOG_LEVEL", "INFO")
        new_session()

        logger = get_logger("test_state_transition_meta")
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(StructuredFormatter())
        logger.logger.addHandler(handler)

        log_state_transition(
            logger, "state1", "state2", metadata={"key": "value"}
        )

        output = stream.getvalue()
        assert "key=value" in output


class TestLogAiCall:
    """Tests for AI call logging functions."""

    def test_log_ai_call_with_response(self, monkeypatch):
        """Test logging AI call with response."""
        monkeypatch.setenv("LOG_LEVEL", "INFO")
        new_session()

        logger = get_logger("test_ai_call")
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(StructuredFormatter())
        logger.logger.addHandler(handler)

        log_ai_call(
            logger,
            prompt="What is 2+2?",
            response="The answer is 4.",
            model="gpt-4",
            duration_ms=100.5,
        )

        output = stream.getvalue()
        assert "AI call completed" in output
        assert "What is 2+2?" in output
        assert "The answer is 4." in output
        assert "gpt-4" in output

    def test_log_ai_response(self, monkeypatch):
        """Test log_ai_response convenience function."""
        monkeypatch.setenv("LOG_LEVEL", "INFO")
        new_session()

        logger = get_logger("test_ai_response")
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(StructuredFormatter())
        logger.logger.addHandler(handler)

        log_ai_response(
            logger,
            prompt="Hello",
            response="Hi there!",
            model="gpt-4",
            duration_ms=50.0,
        )

        output = stream.getvalue()
        assert "AI call completed" in output

    def test_truncates_long_prompts(self, monkeypatch):
        """Test that long prompts are truncated."""
        monkeypatch.setenv("LOG_LEVEL", "INFO")
        new_session()

        logger = get_logger("test_truncate_prompt")
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(StructuredFormatter())
        logger.logger.addHandler(handler)

        long_prompt = "A" * 1000
        log_ai_call(logger, prompt=long_prompt, response="Short", truncate_length=100)

        output = stream.getvalue()
        assert "truncated" in output


class TestWithStateLogging:
    """Tests for the with_state_logging decorator."""

    def test_decorator_logs_entry_and_exit(self, monkeypatch):
        """Test that decorator logs state entry and exit."""
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        new_session()

        logger = get_logger("test_decorator")
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(StructuredFormatter())
        logger.logger.handlers = []  # Clear existing handlers
        logger.logger.addHandler(handler)
        logger.logger.setLevel(logging.DEBUG)

        @with_state_logging(logger, "test_state")
        def my_function():
            return "result"

        result = my_function()

        assert result == "result"
        output = stream.getvalue()
        assert "Entering state: test_state" in output
        assert "Exiting state: test_state" in output

    def test_decorator_logs_errors(self, monkeypatch):
        """Test that decorator logs errors."""
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        new_session()

        logger = get_logger("test_decorator_error")
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(StructuredFormatter())
        logger.logger.handlers = []
        logger.logger.addHandler(handler)
        logger.logger.setLevel(logging.DEBUG)

        @with_state_logging(logger, "error_state")
        def failing_function():
            raise ValueError("Test error")

        with pytest.raises(ValueError, match="Test error"):
            failing_function()

        output = stream.getvalue()
        assert "Error in state error_state" in output

    @pytest.mark.asyncio
    async def test_decorator_works_with_async(self, monkeypatch):
        """Test that decorator works with async functions."""
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        new_session()

        logger = get_logger("test_async_decorator")
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(StructuredFormatter())
        logger.logger.handlers = []
        logger.logger.addHandler(handler)
        logger.logger.setLevel(logging.DEBUG)

        @with_state_logging(logger, "async_state")
        async def async_function():
            return "async_result"

        result = await async_function()

        assert result == "async_result"
        output = stream.getvalue()
        assert "Entering state: async_state" in output
        assert "Exiting state: async_state" in output


class TestConfigLoggingIntegration:
    """Tests for logging integration with config."""

    def test_logging_config_in_config(self, monkeypatch, tmp_path):
        """Test that LoggingConfig is included in Config."""
        from ogd_to_lod.config import LoggingConfig, load_config

        monkeypatch.setenv("TEST_TOKEN", "token")
        monkeypatch.setenv("TEST_KEY", "key")

        config_content = """
github:
  repo: "org/repo"
  token: "${TEST_TOKEN}"

azure:
  endpoint: "https://test.openai.azure.com/"
  api_key: "${TEST_KEY}"
  deployment: "gpt-4"

logging:
  level: "DEBUG"
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        config = load_config(config_file)

        assert config.logging is not None
        assert config.logging.level == "DEBUG"
        assert config.logging.get_level_int() == logging.DEBUG

    def test_logging_config_defaults(self, monkeypatch, tmp_path):
        """Test that logging config has sensible defaults."""
        from ogd_to_lod.config import load_config

        monkeypatch.setenv("TEST_TOKEN", "token")
        monkeypatch.setenv("TEST_KEY", "key")
        monkeypatch.delenv("LOG_LEVEL", raising=False)

        config_content = """
github:
  repo: "org/repo"
  token: "${TEST_TOKEN}"

azure:
  endpoint: "https://test.openai.azure.com/"
  api_key: "${TEST_KEY}"
  deployment: "gpt-4"
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        config = load_config(config_file)

        assert config.logging.level == "INFO"

    def test_log_level_from_env_var(self, monkeypatch, tmp_path):
        """Test that LOG_LEVEL env var is respected in config."""
        from ogd_to_lod.config import load_config

        monkeypatch.setenv("TEST_TOKEN", "token")
        monkeypatch.setenv("TEST_KEY", "key")
        monkeypatch.setenv("LOG_LEVEL", "WARNING")

        config_content = """
github:
  repo: "org/repo"
  token: "${TEST_TOKEN}"

azure:
  endpoint: "https://test.openai.azure.com/"
  api_key: "${TEST_KEY}"
  deployment: "gpt-4"
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        config = load_config(config_file)

        assert config.logging.level == "WARNING"
