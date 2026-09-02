"""FastAPI entry point for the London Biodiversity Expedition Planner."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.biodiversity.api.dependencies import APISettings
from app.biodiversity.api.errors import (
    APIError,
    api_error_handler,
    internal_error_handler,
    validation_error_handler,
)
from app.biodiversity.api.repositories import SQLiteRunCatalog
from app.biodiversity.api.routes.health import router as health_router
from app.biodiversity.api.routes.operations import router as operation_router
from app.biodiversity.api.routes.runs import router as run_router
from app.biodiversity.api.services.operations import Phase4Application
from app.biodiversity.graph import open_biodiversity_sqlite_checkpointer


def create_app(settings: APISettings | None = None) -> FastAPI:
    configured = settings or APISettings.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        catalog = SQLiteRunCatalog(configured.catalog_db)
        catalog.recover_incomplete()
        with open_biodiversity_sqlite_checkpointer(
            configured.checkpoint_db
        ) as checkpointer:
            phase4 = Phase4Application(
                settings=configured,
                catalog=catalog,
                checkpointer=checkpointer,
            )
            app.state.phase4 = phase4
            try:
                await phase4.start()
                yield
            finally:
                await phase4.close()
                catalog.close()

    app = FastAPI(
        title="London Biodiversity Expedition Planner API",
        version="4.0.0",
        description=(
            "A privacy-bounded API for London bird-expedition evidence, "
            "agent execution, human decisions, and checkpoint time travel."
        ),
        lifespan=lifespan,
        docs_url="/docs" if configured.expose_api_docs else None,
        redoc_url="/redoc" if configured.expose_api_docs else None,
        openapi_url="/openapi.json" if configured.expose_api_docs else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(configured.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Last-Event-ID"],
    )
    app.add_exception_handler(APIError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, internal_error_handler)
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(run_router, prefix="/api/v1")
    app.include_router(operation_router, prefix="/api/v1")
    if configured.serve_frontend:
        if not configured.frontend_dist.is_dir():
            raise ValueError("The configured frontend production build does not exist")
        app.mount(
            "/",
            StaticFiles(directory=configured.frontend_dist, html=True),
            name="frontend",
        )
    return app


app = create_app()
