"""Logging configuration helpers."""

import logging
from app.core.config import Settings


def configure_logging(settings: Settings) -> None:
    """Configure process-wide logging for API and ETL workloads.

    Business purpose:
        Provide consistent, structured logs across backend services.
    Why it exists:
        Centralizes logging setup so all components share the same format and level.
    Where used:
        Called during application startup and in CLI/ETL entrypoints.
    Inputs:
        settings: Runtime config containing the desired log level.
    Returns:
        None; sets global logging handlers and levels.
    """
    # Normalize the log level string to a valid logging constant.
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    # Apply a shared log format for all loggers in the process.
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Align uvicorn access logs with the application log level.
    logging.getLogger("uvicorn.access").setLevel(level)
