"""Configuration management with environment variable support."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass
class GitHubConfig:
    """GitHub configuration."""

    repo: str
    token: str


@dataclass
class AzureOpenAIConfig:
    """Azure OpenAI configuration."""

    endpoint: str
    api_key: str
    deployment: str
    api_version: str = "2024-02-15-preview"
    max_requests: int = 50  # Maximum AI requests before asking user to continue
    # Pricing in CHF per 1M tokens
    price_per_1m_input_tokens: float = 1.02
    price_per_1m_output_tokens: float = 8.08
    price_per_1m_cached_tokens: float = 0.10


@dataclass
class SPARQLConfig:
    """SPARQL endpoint configuration."""

    endpoint: str | None = None


@dataclass
class RMLConfig:
    """RML generation configuration."""

    base_uri: str = ""
    rmlmapper_jar: str | None = None
    rmlmapper_use_docker: bool = False
    rmlmapper_docker_image: str = "rmlio/rmlmapper-java:latest"
    yarrrml_parser_docker_image: str = "rmlio/yarrrml-parser:latest"


@dataclass
class LoggingConfig:
    """Logging configuration."""

    level: str = "INFO"

    def get_level_int(self) -> int:
        """Get the logging level as an integer.

        Returns:
            The logging level constant from the logging module.
        """
        import logging

        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "WARN": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        return level_map.get(self.level.upper(), logging.INFO)


@dataclass
class Config:
    """Main application configuration."""

    github: GitHubConfig
    azure: AzureOpenAIConfig
    sparql: SPARQLConfig = field(default_factory=SPARQLConfig)
    rml: RMLConfig = field(default_factory=RMLConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def _substitute_env_vars(value: str) -> str:
    """Substitute ${VAR} or $VAR patterns with environment variables.

    Args:
        value: String potentially containing environment variable references.

    Returns:
        String with environment variables substituted.

    Raises:
        ValueError: If a referenced environment variable is not set.
    """
    pattern = r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)"

    def replacer(match: re.Match) -> str:
        var_name = match.group(1) or match.group(2)
        env_value = os.environ.get(var_name)
        if env_value is None:
            raise ValueError(f"Environment variable '{var_name}' is not set")
        return env_value

    return re.sub(pattern, replacer, value)


def _process_config_values(obj: dict | list | str) -> dict | list | str:
    """Recursively process config values, substituting environment variables.

    Args:
        obj: Configuration object (dict, list, or string).

    Returns:
        Processed configuration with environment variables substituted.
    """
    if isinstance(obj, dict):
        return {k: _process_config_values(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_process_config_values(item) for item in obj]
    elif isinstance(obj, str):
        return _substitute_env_vars(obj)
    return obj


def load_config(config_path: str | Path) -> Config:
    """Load configuration from YAML file with environment variable substitution.

    Args:
        config_path: Path to the configuration YAML file.

    Returns:
        Loaded and validated configuration.

    Raises:
        FileNotFoundError: If the configuration file does not exist.
        ValueError: If required configuration values are missing or invalid.
    """
    # Load environment variables from .env file if present
    load_dotenv()

    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_path) as f:
        raw_config = yaml.safe_load(f)

    if raw_config is None:
        raise ValueError("Configuration file is empty")

    # Substitute environment variables
    config_data = _process_config_values(raw_config)

    # Validate and construct config objects
    try:
        github_data = config_data.get("github", {})
        # Support environment variable override for GitHub repo
        github_repo = os.environ.get("GITHUB_REPO", "") or github_data.get("repo", "")
        github = GitHubConfig(
            repo=github_repo,
            token=github_data.get("token", ""),
        )
        if not github.repo:
            raise ValueError("github.repo is required")
        if not github.token:
            raise ValueError("github.token is required")

        azure_data = config_data.get("azure", {})
        pricing_data = azure_data.get("pricing", {})
        azure = AzureOpenAIConfig(
            endpoint=azure_data.get("endpoint", ""),
            api_key=azure_data.get("api_key", ""),
            deployment=azure_data.get("deployment", ""),
            api_version=azure_data.get("api_version", "2024-02-15-preview"),
            max_requests=azure_data.get("max_requests", 50),
            price_per_1m_input_tokens=pricing_data.get("price_per_1m_input_tokens", 1.02),
            price_per_1m_output_tokens=pricing_data.get("price_per_1m_output_tokens", 8.08),
            price_per_1m_cached_tokens=pricing_data.get("price_per_1m_cached_tokens", 0.10),
        )
        if not azure.endpoint:
            raise ValueError("azure.endpoint is required")
        if not azure.api_key:
            raise ValueError("azure.api_key is required")
        if not azure.deployment:
            raise ValueError("azure.deployment is required")

        sparql_data = config_data.get("sparql", {})
        sparql = SPARQLConfig(
            endpoint=sparql_data.get("endpoint"),
        )

        rml_data = config_data.get("rml", {})
        # Support environment variable for RMLMapper JAR path
        rmlmapper_jar = rml_data.get("rmlmapper_jar") or os.environ.get("RMLMAPPER_JAR")
        rml = RMLConfig(
            base_uri=rml_data.get("base_uri", ""),
            rmlmapper_jar=rmlmapper_jar,
            rmlmapper_use_docker=rml_data.get("rmlmapper_use_docker", False),
            rmlmapper_docker_image=rml_data.get(
                "rmlmapper_docker_image", "rmlio/rmlmapper-java:latest"
            ),
            yarrrml_parser_docker_image=rml_data.get(
                "yarrrml_parser_docker_image", "rmlio/yarrrml-parser:latest"
            ),
        )

        # Logging config - can also be set via LOG_LEVEL environment variable
        logging_data = config_data.get("logging", {})
        log_level = logging_data.get("level", os.environ.get("LOG_LEVEL", "INFO"))
        logging_config = LoggingConfig(level=log_level)

        return Config(
            github=github,
            azure=azure,
            sparql=sparql,
            rml=rml,
            logging=logging_config,
        )

    except KeyError as e:
        raise ValueError(f"Missing required configuration key: {e}")
