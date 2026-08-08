"""FastAPI application factory.

The API is the whole product surface — the Streamlit UI is one client of it, so
a React/Next.js frontend can be added later without touching this layer.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.api.routes import admin, companies, dashboard, jobs, outreach, recruiters, settings_routes
from app.config import settings
from app.logging_config import configure_logging, get_logger
from app.security.rate_limit import FixedWindowRateLimiter

log = get_logger(__name__)

DESCRIPTION = """
Research and outreach intelligence for job seekers.

Answers one question well: **who should I contact today about a role that is
actively hiring?**

This is not a mass-emailing tool. It discovers jobs from official public APIs
and companies' own career pages, finds *publicly published* talent contacts,
scores relevance transparently, and requires explicit human approval before any
outreach is recorded. It never sends email itself.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    configure_logging()
    log.info(
        "app.startup",
        env=settings.app_env,
        ai_configured=settings.ai_configured,
        verification_provider=settings.email_verification_provider,
        auth_enabled=settings.auth_enabled,
    )
    scheduler = None
    if settings.scheduler_enabled:
        from app.workers.scheduler import start_scheduler

        scheduler = start_scheduler()
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        log.info("app.shutdown")


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # The Streamlit frontend runs on a different port; keep this tight.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:8501",
            "http://127.0.0.1:8501",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["X-API-Key", "Content-Type"],
    )

    limiter = FixedWindowRateLimiter(settings.api_rate_limit_per_minute)
    app.state.rate_limiter = limiter

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Any]]
    ) -> Any:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)
        started = time.monotonic()

        client_id = request.client.host if request.client else "unknown"
        if request.url.path.startswith("/api") and not limiter.allow(client_id):
            structlog.contextvars.clear_contextvars()
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Rate limit exceeded. Slow down and try again shortly."},
                headers={"Retry-After": "60", "X-Request-ID": request_id},
            )

        try:
            response = await call_next(request)
        except Exception:
            log.exception("request.unhandled_error", method=request.method)
            structlog.contextvars.clear_contextvars()
            # Never leak internals (or secrets) into an HTTP response body.
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "detail": "Internal server error",
                    "request_id": request_id,
                },
                headers={"X-Request-ID": request_id},
            )

        elapsed_ms = int((time.monotonic() - started) * 1000)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        log.info(
            "request.complete",
            method=request.method,
            status=response.status_code,
            elapsed_ms=elapsed_ms,
        )
        structlog.contextvars.clear_contextvars()
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Surface the *validation* message (useful, e.g. rejected URLs) without
        # echoing the whole request body back.
        problems = [
            {
                "field": ".".join(str(p) for p in error.get("loc", []) if p != "body"),
                "message": error.get("msg", "invalid value"),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "Validation failed", "problems": problems},
        )

    @app.exception_handler(IntegrityError)
    async def integrity_handler(request: Request, exc: IntegrityError) -> JSONResponse:
        log.warning("db.integrity_error", error=str(exc.orig)[:300])
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "That record conflicts with one that already exists."},
        )

    @app.exception_handler(SQLAlchemyError)
    async def db_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        log.exception("db.error")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "Database error. Check the service logs."},
        )

    api_routers = [
        companies.router,
        jobs.router,
        recruiters.router,
        outreach.router,
        dashboard.router,
        settings_routes.router,
        admin.router,
    ]
    for router in api_routers:
        app.include_router(router, prefix="/api")

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {
            "app": settings.app_name,
            "docs": "/docs",
            "health": "/api/admin/health",
        }

    return app


app = create_app()
