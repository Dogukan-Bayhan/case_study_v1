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
    """Create and configure the FastAPI application for all backend APIs.

    Business purpose:
        Provide a single entrypoint that wires analytics, auth, ETL, quality,
        and tenant management APIs into one production service.
    Why it exists:
        Centralizes runtime initialization so dependencies (DB, ClickHouse)
        and routers are consistently registered in every environment.
    Where used:
        Called by the ASGI server on startup and by tests that need an app.
    Inputs:
        settings: Optional Settings override; when None, load defaults.
    Returns:
        Fully configured FastAPI application instance.
    """
    # Resolve runtime configuration and enable structured logging early.
    settings = settings or get_settings()
    configure_logging(settings)

    # FastAPI instance is the root of all HTTP routing and dependency state.
    app = FastAPI(title=settings.app_name)

    # Database engine/session maker are shared via app.state for DI usage.
    engine = create_engine_from_settings(settings)
    app.state.settings = settings
    app.state.db_engine = engine
    app.state.session_maker = get_session_maker(engine)

    @app.on_event("startup")
    def _startup() -> None:
        """Initialize ClickHouse schema required for analytics and ETL.

        Business purpose:
            Ensure analytics tables exist before any ingestion or queries run.
        Why it exists:
            ClickHouse schema must be present for ETL and analytics endpoints.
        Where used:
            Invoked automatically by FastAPI during application startup.
        Inputs:
            None; uses resolved settings from outer scope.
        Returns:
            None; side effect is schema verification and table creation.
        """
        if settings.clickhouse_enabled:
            # Lazily connect and ensure schema is present before serving traffic.
            client = get_clickhouse_client(settings)
            try:
                # Create or evolve analytics tables in a single startup pass.
                ensure_clickhouse_schema(client, settings)
            finally:
                # Always close the client to avoid leaking network resources.
                client.disconnect()

    # Register all API routers to expose backend capabilities.
    app.include_router(auth_router)
    app.include_router(tenants_router)
    app.include_router(etl_router)
    app.include_router(quality_router)
    app.include_router(analytics_router)
    app.include_router(web_router)

    # Static assets are served from the packaged web directory.
    app.mount("/static", StaticFiles(directory="/app/app/web/static"), name="static")

    return app


app = create_app()
