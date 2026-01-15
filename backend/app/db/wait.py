"""Wait for the primary database to become available."""

from sqlalchemy import text
from tenacity import retry, stop_after_attempt, wait_fixed

from app.core.config import get_settings
from app.db.session import create_engine_from_settings


@retry(stop=stop_after_attempt(20), wait=wait_fixed(2))
def wait_for_db() -> None:
    """Block startup until the primary database is reachable."""
    settings = get_settings()
    engine = create_engine_from_settings(settings)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


if __name__ == "__main__":
    wait_for_db()
