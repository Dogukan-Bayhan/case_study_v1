"""Logging configuration helpers."""

import logging
from app.core.config import Settings


def configure_logging(settings: Settings) -> None:
    """Apply a consistent log format early so ETL and API share the same baseline."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("uvicorn.access").setLevel(level)
