"""FastAPI entry point."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.api import insights, network, operations
from app.api.deps import ApiError
from app.core.config import Settings, get_settings
from app.core.database import Database
from app.core.runtime import Runtime

logger = logging.getLogger(__name__)


def error_body(code: str, message: str, details: object = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime = Runtime(settings, database)
        app.state.runtime = runtime
        await asyncio.to_thread(runtime.startup)
        runtime.ws.bind(asyncio.get_running_loop())
        task = asyncio.create_task(runtime.broadcast_loop())
        logger.info("Backend ready. Transport: %s, database: %s", runtime.transport, runtime.database.backend)
        yield
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await asyncio.to_thread(runtime.shutdown)

    app = FastAPI(
        title="Smart Traffic Management System",
        description="Software-simulated IoT traffic network. All sensors and vehicles are virtual.",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[f"http://localhost:{settings.frontend_port}", f"http://127.0.0.1:{settings.frontend_port}"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(operations.router)
    app.include_router(network.router)
    app.include_router(insights.router)

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(error_body(error.code, error.message), status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
        details = [
            {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in error.errors()
        ]
        message = details[0]["message"] if details else "Invalid request"
        return JSONResponse(error_body("validation_error", message, details), status_code=422)

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, error: HTTPException) -> JSONResponse:
        return JSONResponse(error_body("http_error", str(error.detail)), status_code=error.status_code)

    @app.websocket("/ws/live")
    async def live(socket: WebSocket) -> None:
        runtime: Runtime = socket.app.state.runtime
        hello = [
            ("topology", runtime.engine.topology()),
            ("series", list(runtime.processor.series)),
            ("sensors", runtime.processor.sensor_view()),
            ("live", await asyncio.to_thread(runtime._live_payload)),
        ]
        await runtime.ws.serve(socket, hello)

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {"service": "smart-traffic-management", "docs": "/docs", "simulated": True}

    return app


app = create_app()
