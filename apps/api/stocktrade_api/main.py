from __future__ import annotations

from fastapi import FastAPI

from .routes.health import router as health_router

API_TITLE = "StockTradebyZ API"
API_VERSION = "0.1.0"


def create_app() -> FastAPI:
    app = FastAPI(title=API_TITLE, version=API_VERSION)
    app.include_router(health_router, prefix="/api")
    return app


app = create_app()
