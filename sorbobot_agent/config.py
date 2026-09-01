"""Configuration for sorbobot-agent — read from environment variables."""

import os
from dataclasses import dataclass, field


@dataclass
class MCPToolboxConfig:
    url: str = field(default_factory=lambda: os.environ["MCP_TOOLBOX_URL"])
    # Shared toolset, also used by other consumers of the MCP toolbox —
    # left untouched so changing SorboBot's setup doesn't affect them.
    toolset: str = field(
        default_factory=lambda: os.getenv("MCP_TOOLBOX_TOOLSET", "crisalid-sorbobot")
    )
    # SorboBot-specific toolset (sorbobot-* tools only). Loaded in addition
    # to `toolset` above — see McpToolboxClient.aopen().
    sorbobot_toolset: str = field(
        default_factory=lambda: os.getenv("SORBOBOT_TOOLSET", "sorbobot")
    )


@dataclass
class ValidationConfig:
    # LLM judge — semantic relevance score returned by the LLM (0-1).
    # Default matches .env.sample — keep both in sync.
    judge_threshold: float = field(
        default_factory=lambda: float(os.getenv("JUDGE_THRESHOLD", "0.7"))
    )
    # Minimum Document-HAS_DOMAIN similarity counted when searching domains (0-1).
    # Default matches .env.sample — keep both in sync.
    semantic_threshold: float = field(
        default_factory=lambda: float(os.getenv("SEMANTIC_THRESHOLD", "0.53"))
    )
    # Number of domain candidates returned by the syntactic search.
    top_k_syntactic: int = field(
        default_factory=lambda: int(os.getenv("SORBOBOT_TOP_K_SYNTACTIC", "20"))
    )
    # Minimum total documents across matched Topics before broadening the
    # expert search to their parent SubField (see domain_tools._resolve_search_scope).
    domain_min_docs: int = field(
        default_factory=lambda: int(os.getenv("DOMAIN_MIN_DOCS", "5"))
    )
    max_input_length: int = field(
        default_factory=lambda: int(os.getenv("MAX_INPUT_LENGTH", "500"))
    )


@dataclass
class DisplayConfig:
    show_max_authors: int = field(
        default_factory=lambda: int(os.getenv("SHOW_MAX_AUTHORS", "10"))
    )
    show_max_domains: int = field(
        default_factory=lambda: int(os.getenv("SHOW_MAX_DOMAINS", "5"))
    )


@dataclass
class CrisalidTaxiConfig:
    base_url: str = field(default_factory=lambda: os.environ["CRISALID_TAXI_URL"])


@dataclass
class LoggingConfig:
    # Write daily diagnostic log files in addition to the console — see
    # logging_config.configure_logging().
    log_to_file: bool = field(
        default_factory=lambda: os.getenv("LOG_TO_FILE", "false").strip().lower()
        in ("1", "true", "yes")
    )
    # Directory for log files when log_to_file is set. Empty string means the
    # default <project root>/logs.
    log_dir: str = field(default_factory=lambda: os.getenv("LOG_DIR", ""))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))


@dataclass
class AppConfig:
    mcp_toolbox: MCPToolboxConfig = field(default_factory=MCPToolboxConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    crisalid_taxi: CrisalidTaxiConfig = field(default_factory=CrisalidTaxiConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)