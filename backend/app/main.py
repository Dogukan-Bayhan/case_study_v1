"""FastAPI application entrypoint."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.analytics.router import router as analytics_router
from app.auth.router import router as auth_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.clickhouse import ensure_clickhouse_schema, get_clickhouse_client
from app.db.session import create_engine_from_settings, get_session_maker
from app.etl.router import router as etl_router
from app.quality.router import router as quality_router
from app.tenants.router import router as tenants_router
from app.web.router import router as web_router


def create_app(settings: Settings | None = None) -> FastAPI:
    """Wire FastAPI with persistence, routers, and shared runtime state."""
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(title=settings.app_name)

    engine = create_engine_from_settings(settings)
    app.state.settings = settings
    app.state.db_engine = engine
    app.state.session_maker = get_session_maker(engine)

    @app.on_event("startup")
    def _startup() -> None:
        """Ensure analytics storage is ready before serving requests."""
        if settings.clickhouse_enabled:
            client = get_clickhouse_client(settings)
            try:
                # Create/upgrade ClickHouse tables required by ETL and analytics.
                ensure_clickhouse_schema(client, settings)
            finally:
                client.disconnect()

    app.include_router(auth_router)
    app.include_router(tenants_router)
    app.include_router(etl_router)
    app.include_router(quality_router)
    app.include_router(analytics_router)
    app.include_router(web_router)

    app.mount("/static", StaticFiles(directory="/app/app/web/static"), name="static")

    return app


app = create_app()
