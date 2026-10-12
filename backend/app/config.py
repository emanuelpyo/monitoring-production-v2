"""Application configuration loaded from environment variables.

All settings have safe local-development defaults so the service can boot
without an explicitly sourced .env file. Values are read once at import time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL",
            "postgresql://barcode@localhost:5433/production_monitoring",
        )
    )
    auth_secret: str = field(default_factory=lambda: os.getenv("AUTH_SECRET", "change-me-dev-secret"))
    token_ttl_seconds: int = field(default_factory=lambda: _int_env("TOKEN_TTL_SECONDS", 28800))

    backend_host: str = field(default_factory=lambda: os.getenv("BACKEND_HOST", "0.0.0.0"))
    backend_port: int = field(default_factory=lambda: _int_env("BACKEND_PORT", 8000))
    cors_allow_origins: list[str] = field(
        default_factory=lambda: [
            item.strip()
            for item in os.getenv(
                "CORS_ALLOW_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173",
            ).split(",")
            if item.strip()
        ]
    )

    generator_enabled: bool = field(default_factory=lambda: _bool_env("GENERATOR_ENABLED", True))
    generator_interval_ms: int = field(default_factory=lambda: _int_env("GENERATOR_INTERVAL_MS", 2500))
    generator_success_rate: float = field(default_factory=lambda: _float_env("GENERATOR_SUCCESS_RATE", 0.92))
    scanner_count: int = field(default_factory=lambda: _int_env("SCANNER_COUNT", 4))

    seed_hours: int = field(default_factory=lambda: _int_env("SEED_HOURS", 24))
    seed_events_per_hour: int = field(default_factory=lambda: _int_env("SEED_EVENTS_PER_HOUR", 120))

    display_tz: str = field(default_factory=lambda: os.getenv("DISPLAY_TZ", "Asia/Jakarta"))

    def safe_dict(self) -> dict[str, object]:
        """A representation safe for logs / health responses (no secrets)."""
        return {
            "database_configured": bool(self.database_url),
            "display_tz": self.display_tz,
            "generator_enabled": self.generator_enabled,
            "generator_interval_ms": self.generator_interval_ms,
            "scanner_count": self.scanner_count,
            "seed_hours": self.seed_hours,
        }


settings = Settings()
