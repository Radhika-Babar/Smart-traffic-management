"""Environment configuration. All values are local; no external credentials exist."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BACKEND_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(BACKEND_DIR.parent / ".env", ".env"), extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    frontend_port: int = 5173

    database_url: str = f"sqlite:///{(DATA_DIR / 'traffic.db').as_posix()}"

    mqtt_enabled: bool = True
    mqtt_broker_host: str = "127.0.0.1"
    mqtt_broker_port: int = 1883

    simulation_interval: float = 0.5  # seconds of simulated time per tick
    simulation_autostart: bool = True
    simulation_seed: int | None = None
    seed_history_days: int = 14


@lru_cache
def get_settings() -> Settings:
    return Settings()
