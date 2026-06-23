

import logging
import sys
from datetime import date
from pathlib import Path

from sorbobot_agent.config import LoggingConfig

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_LOG_DIR = _PROJECT_ROOT / "logs"

_configured = False


def configure_logging(config: LoggingConfig) -> None:
    """Idempotent — safe to call every time a SorboBotAgent() is created."""
    global _configured
    if _configured:
        return
    _configured = True

    logger = logging.getLogger("sorbobot_agent")
    logger.setLevel(config.log_level.upper())
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if config.log_to_file:
        log_dir = Path(config.log_dir) if config.log_dir else _DEFAULT_LOG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"sorbobot-{date.today().isoformat()}.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.info("File logging enabled — writing to %s", log_file)
