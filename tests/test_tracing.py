"""Tests for LangSmith tracing configuration."""

import os

import pytest

from ogd_to_lod.tracing import EU_ENDPOINT, configure_tracing, get_trace_metadata


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Remove LangSmith env vars before each test."""
    for var in ("LANGSMITH_API_KEY", "LANGSMITH_TRACING", "LANGSMITH_ENDPOINT"):
        monkeypatch.delenv(var, raising=False)


class TestConfigureTracing:
    def test_enabled_when_api_key_set(self, monkeypatch):
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_test_key")

        result = configure_tracing()

        assert result is True
        assert os.environ["LANGSMITH_TRACING"] == "true"

    def test_disabled_when_key_absent(self):
        result = configure_tracing()

        assert result is False
        assert os.environ.get("LANGSMITH_TRACING") is None

    def test_eu_endpoint_set_as_default(self, monkeypatch):
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_test_key")

        configure_tracing()

        assert os.environ["LANGSMITH_ENDPOINT"] == EU_ENDPOINT

    def test_existing_endpoint_not_overwritten(self, monkeypatch):
        custom_endpoint = "https://api.smith.langchain.com"
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_test_key")
        monkeypatch.setenv("LANGSMITH_ENDPOINT", custom_endpoint)

        configure_tracing()

        assert os.environ["LANGSMITH_ENDPOINT"] == custom_endpoint


class TestGetTraceMetadata:
    def test_returns_all_keys(self, monkeypatch):
        # Set a session ID via the logging module
        from ogd_to_lod.logging import set_session_id

        set_session_id("test-session")

        metadata = get_trace_metadata(
            csv_path="/data/test.csv",
            base_uri="http://example.org/",
            flow_state="propose",
        )

        assert metadata["session_id"] == "test-session"
        assert metadata["csv_path"] == "/data/test.csv"
        assert metadata["base_uri"] == "http://example.org/"
        assert metadata["flow_state"] == "propose"

    def test_omits_none_values(self):
        metadata = get_trace_metadata()

        assert "csv_path" not in metadata
        assert "base_uri" not in metadata
        assert "flow_state" not in metadata

    def test_partial_metadata(self):
        metadata = get_trace_metadata(csv_path="/data/test.csv")

        assert metadata["csv_path"] == "/data/test.csv"
        assert "base_uri" not in metadata
        assert "flow_state" not in metadata
