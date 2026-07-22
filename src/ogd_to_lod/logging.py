"""Logging and tracing utilities for debugging.

This module provides structured logging with session IDs for tracing,
state transition logging for LangGraph, and AI call logging.
"""

import logging
import os
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable

from dotenv import load_dotenv

# Context variable for session ID - allows async-safe session tracking
_session_id: ContextVar[str] = ContextVar("session_id", default="")

# Default truncation length for AI prompts/responses
DEFAULT_TRUNCATE_LENGTH = 500


def get_session_id() -> str:
    """Get the current session ID.

    Returns:
        The current session ID, or empty string if not set.
    """
    return _session_id.get()


def set_session_id(session_id: str | None = None) -> str:
    """Set a new session ID.

    Args:
        session_id: Optional session ID to set. If None, generates a new UUID.

    Returns:
        The session ID that was set.
    """
    if session_id is None:
        session_id = str(uuid.uuid4())[:8]  # Use short UUID for readability
    _session_id.set(session_id)
    return session_id


def new_session() -> str:
    """Start a new session with a fresh UUID.

    Returns:
        The newly generated session ID.
    """
    return set_session_id()


class StructuredFormatter(logging.Formatter):
    """Custom formatter that includes timestamp, session ID, and structured fields."""

    def __init__(self) -> None:
        """Initialize the formatter with a structured format string."""
        super().__init__()
        self._fmt = "[{timestamp}] [{level}] [{session}] {name}: {message}"

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record with structured fields.

        Args:
            record: The log record to format.

        Returns:
            Formatted log string.
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        session = get_session_id() or "no-session"

        # Build the base message
        message = record.getMessage()

        # Add extra fields if present
        extra_fields = getattr(record, "extra_fields", None)
        if extra_fields:
            extra_str = " | ".join(f"{k}={v}" for k, v in extra_fields.items())
            message = f"{message} | {extra_str}"

        return self._fmt.format(
            timestamp=timestamp,
            level=record.levelname,
            session=session,
            name=record.name,
            message=message,
        )


class SessionLogger(logging.LoggerAdapter):
    """Logger adapter that automatically includes session context."""

    def process(
        self, msg: str, kwargs: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """Process the logging call to add extra context.

        Args:
            msg: The log message.
            kwargs: Additional keyword arguments.

        Returns:
            Tuple of (message, kwargs) with extra context added.
        """
        # Extract extra fields from kwargs if present
        extra = kwargs.get("extra", {})
        if "extra_fields" in kwargs:
            extra["extra_fields"] = kwargs.pop("extra_fields")
            kwargs["extra"] = extra
        return msg, kwargs


def get_log_level() -> int:
    """Get the configured log level from environment.

    Returns:
        The logging level (e.g., logging.DEBUG, logging.INFO).
    """
    load_dotenv()
    level_str = os.environ.get("LOG_LEVEL", "INFO").upper()
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "WARN": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    return level_map.get(level_str, logging.INFO)


def setup_logging(name: str = "ogd_to_lod") -> SessionLogger:
    """Set up and return a configured logger.

    Args:
        name: The logger name, typically the module name.

    Returns:
        A configured SessionLogger instance.
    """
    logger = logging.getLogger(name)

    # Only configure if not already configured
    if not logger.handlers:
        logger.setLevel(get_log_level())

        # Console handler with structured formatter
        console_handler = logging.StreamHandler()
        console_handler.setLevel(get_log_level())
        console_handler.setFormatter(StructuredFormatter())
        logger.addHandler(console_handler)

        # Prevent propagation to root logger to avoid duplicate logs
        logger.propagate = False

    return SessionLogger(logger, {})


def get_logger(name: str = "ogd_to_lod") -> SessionLogger:
    """Get or create a logger with the given name.

    This is the main entry point for getting a logger in the application.

    Args:
        name: The logger name, typically the module name.

    Returns:
        A configured SessionLogger instance.
    """
    return setup_logging(name)


def truncate_text(text: str, max_length: int = DEFAULT_TRUNCATE_LENGTH) -> str:
    """Truncate text for readable logging.

    Args:
        text: The text to truncate.
        max_length: Maximum length before truncation.

    Returns:
        Truncated text with ellipsis if needed.
    """
    if len(text) <= max_length:
        return text
    return text[:max_length] + f"... [truncated, {len(text)} chars total]"


def log_state_transition(
    logger: SessionLogger,
    from_state: str,
    to_state: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log a LangGraph state transition.

    Args:
        logger: The logger instance to use.
        from_state: The state transitioning from.
        to_state: The state transitioning to.
        metadata: Optional additional metadata about the transition.
    """
    extra_fields = {
        "from_state": from_state,
        "to_state": to_state,
    }
    if metadata:
        extra_fields.update(metadata)

    logger.info(
        f"State transition: {from_state} -> {to_state}",
        extra={"extra_fields": extra_fields},
    )


def log_ai_call(
    logger: SessionLogger,
    prompt: str,
    response: str | None = None,
    model: str | None = None,
    duration_ms: float | None = None,
    truncate_length: int = DEFAULT_TRUNCATE_LENGTH,
) -> None:
    """Log an AI prompt and response.

    Args:
        logger: The logger instance to use.
        prompt: The prompt sent to the AI.
        response: The response from the AI (optional, for logging before call).
        model: The model name/identifier.
        duration_ms: Call duration in milliseconds.
        truncate_length: Maximum length for prompt/response text.
    """
    extra_fields: dict[str, Any] = {
        "prompt_summary": truncate_text(prompt, truncate_length),
    }

    if model:
        extra_fields["model"] = model

    if response is not None:
        extra_fields["response_summary"] = truncate_text(response, truncate_length)

    if duration_ms is not None:
        extra_fields["duration_ms"] = f"{duration_ms:.2f}"

    if response is not None:
        logger.info("AI call completed", extra={"extra_fields": extra_fields})
    else:
        logger.debug("AI call initiated", extra={"extra_fields": extra_fields})


def log_ai_request(
    logger: SessionLogger,
    prompt: str,
    model: str | None = None,
    truncate_length: int = DEFAULT_TRUNCATE_LENGTH,
) -> None:
    """Log an AI request (before the call).

    Args:
        logger: The logger instance to use.
        prompt: The prompt being sent.
        model: The model name/identifier.
        truncate_length: Maximum length for prompt text.
    """
    log_ai_call(logger, prompt, model=model, truncate_length=truncate_length)


def log_ai_response(
    logger: SessionLogger,
    prompt: str,
    response: str,
    model: str | None = None,
    duration_ms: float | None = None,
    truncate_length: int = DEFAULT_TRUNCATE_LENGTH,
) -> None:
    """Log an AI response (after the call).

    Args:
        logger: The logger instance to use.
        prompt: The original prompt.
        response: The AI response.
        model: The model name/identifier.
        duration_ms: Call duration in milliseconds.
        truncate_length: Maximum length for text.
    """
    log_ai_call(
        logger, prompt, response, model, duration_ms, truncate_length
    )


def with_state_logging(
    logger: SessionLogger,
    state_name: str,
) -> Callable:
    """Decorator to log entry and exit from a state/node function.

    Args:
        logger: The logger instance to use.
        state_name: The name of the state/node.

    Returns:
        A decorator function.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            logger.debug(f"Entering state: {state_name}")
            try:
                result = func(*args, **kwargs)
                logger.debug(f"Exiting state: {state_name}")
                return result
            except Exception as e:
                logger.error(f"Error in state {state_name}: {e}")
                raise

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            logger.debug(f"Entering state: {state_name}")
            try:
                result = await func(*args, **kwargs)
                logger.debug(f"Exiting state: {state_name}")
                return result
            except Exception as e:
                logger.error(f"Error in state {state_name}: {e}")
                raise

        # Return appropriate wrapper based on function type
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return wrapper

    return decorator


# Module-level logger for convenience
_default_logger: SessionLogger | None = None


def get_default_logger() -> SessionLogger:
    """Get the default module-level logger.

    Returns:
        The default logger instance.
    """
    global _default_logger
    if _default_logger is None:
        _default_logger = get_logger()
    return _default_logger
