"""Wait for the primary database to become available."""

from sqlalchemy import text
from tenacity import retry, stop_after_attempt, wait_fixed

from app.core.config import get_settings
from app.db.session import create_engine_from_settings


@retry(stop=stop_after_attempt(20), wait=wait_fixed(2))
def wait_for_db() -> None:
    """Wait for the primary relational database to become reachable.

    Business purpose:
        Ensure the application does not start before the database is ready.
    Why it exists:
        Avoids startup errors when the database container is still initializing.
    Where used:
        Startup scripts and container entrypoints.
    Inputs:
        None; uses environment-driven settings.
    Returns:
        None; exits once a connection can be established.
    """
    settings = get_settings()
    engine = create_engine_from_settings(settings)
    with engine.connect() as conn:
        # Query is a minimal connectivity check against the primary database.
        # SELECT 1 avoids touching application tables or heavy scans.
        # Used as a low-latency readiness probe during startup.
        conn.execute(text("SELECT 1"))


if __name__ == "__main__":
    wait_for_db()
