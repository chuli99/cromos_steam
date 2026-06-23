"""Configuración central del backend.

Todos los valores se pueden sobrescribir por variables de entorno con el prefijo
``SCP_`` o mediante un archivo ``.env`` (ver ``.env.example``).
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SCP_",
        extra="ignore",
    )

    # --- Moneda / localización ---
    currency: int = 1            # 1 = USD (recomendado por estabilidad)
    country_code: str = "ar"     # código de país para appdetails
    language: str = "spanish"    # idioma para appdetails

    # --- TTLs de caché (segundos) ---
    cache_ttl_cards: int = 6 * 3600     # precio de cromos: 6h
    cache_ttl_game: int = 1 * 3600      # precio del juego: 1h
    cache_ttl_card_list: int = 24 * 3600  # lista de cromos: 24h

    # --- Rate limiting por host de Steam (intervalo mínimo entre requests, s) ---
    # Cada host se limita por separado. Se eligen BIEN POR DEBAJO del máximo teórico:
    # el límite de priceoverview es una ventana deslizante que, sostenida cerca del
    # tope (~20 req/min), igual se dispara y bloquea la IP por varios minutos. Por eso
    # community va a ~12 req/min (5s): en escaneos largos (cientos de boosters/juegos)
    # previene el bloqueo en vez de tener que absorberlo.
    community_interval: float = 6.0  # steamcommunity.com (priceoverview/search): ~10 req/min
    store_interval: float = 2.0      # store.steampowered.com (appdetails): ~150 req/5min
    throttle_concurrency: int = 1    # requests concurrentes por host
    # Pocos reintentos y cooldown corto: una request NO debe colgarse minutos esperando.
    # El cooldown igual queda "pegado" al host (espacia las siguientes requests), así no
    # se pierde la protección, y el cliente reintenta lo que falló (la caché evita repetir).
    max_retries: int = 3             # reintentos ante 429 / 5xx / red
    backoff_base: float = 2.0        # base del backoff exponencial (s)
    backoff_max: float = 30.0        # tope del backoff / Retry-After (s)
    cooldown_429: float = 20.0       # pausa del host ante un 429 sin Retry-After (s)

    # --- Cálculo del profit ---
    fee_rate: float = 0.15   # fee de Steam (5% + 10%)
    drop_ratio: float = 0.5  # proporción del set que dropea (la mitad)

    # --- HTTP ---
    http_timeout: float = 15.0
    user_agent: str = (
        "SteamCardProfit/1.0 (+https://github.com/chuli99/cromos_steam)"
    )

    # --- Endpoints base de Steam ---
    steam_store_base: str = "https://store.steampowered.com"
    steam_community_base: str = "https://steamcommunity.com"
    cards_appid: int = 753  # todos los cromos viven bajo este appid

    # --- CORS ---
    # Lista de origins separada por coma. La extensión (chrome-extension://) se
    # permite aparte por regex en main.py.
    cors_origins_raw: str = "http://localhost,http://localhost:8000"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]


# Instancia única reutilizada en toda la app.
settings = Settings()
