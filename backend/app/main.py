"""App FastAPI: CORS, montaje de routers y ciclo de vida del cliente httpx."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import booster, profit
from .steam.client import close_client


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # startup: nada que precargar (el cliente httpx se crea de forma perezosa).
    yield
    # shutdown: cerrar el cliente httpx compartido.
    await close_client()


app = FastAPI(
    title="Steam Card Profit Analyzer",
    version="1.0.0",
    description="Proxy + caché + rate limiter hacia Steam para calcular profit de cromos.",
    lifespan=lifespan,
)

# CORS: la extensión (chrome-extension://*) y localhost en desarrollo.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=r"chrome-extension://.*",
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(profit.router, prefix="/api")
app.include_router(booster.router, prefix="/api")


@app.get("/health")
async def health() -> dict[str, str]:
    """Healthcheck simple."""
    return {"status": "ok"}
