from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


class ConfigurationError(ValueError):
    pass


def split_csv(value: str) -> list[str]:
    return list(dict.fromkeys(x.strip() for x in value.split(",") if x.strip()))


@dataclass
class Settings:
    search_provider: str = "auto"
    brave_key: str = field(default="", repr=False)
    tavily_key: str = field(default="", repr=False)
    openai_key: str = field(default="", repr=False)
    openai_model: str = ""
    state_dir: Path = Path(".conference-agent")
    request_interval: float = 1.2
    timeout: float = 25
    search_cache_seconds: float = 43200
    source_cache_seconds: float = 3600
    blocked_domains: tuple[str, ...] = ()
    official_domains: tuple[str, ...] = ()
    contact: str = ""

    @classmethod
    def load(cls, env_file: Path | None = None) -> Settings:
        # Explicit local file only; do not accidentally load a parent project's secrets.
        load_dotenv(env_file or Path.cwd() / ".env", override=False)
        try:
            result = cls(
                search_provider=os.getenv("SEARCH_PROVIDER", "auto").lower(),
                brave_key=os.getenv("BRAVE_SEARCH_API_KEY", ""),
                tavily_key=os.getenv("TAVILY_API_KEY", ""),
                openai_key=os.getenv("OPENAI_API_KEY", ""),
                openai_model=os.getenv("OPENAI_MODEL", ""),
                state_dir=Path(os.getenv("CONFERENCE_STATE_DIR", ".conference-agent")),
                request_interval=float(os.getenv("REQUEST_INTERVAL_SECONDS", "1.2")),
                timeout=float(os.getenv("HTTP_TIMEOUT_SECONDS", "25")),
                search_cache_seconds=float(os.getenv("SEARCH_CACHE_HOURS", "12")) * 3600,
                source_cache_seconds=float(os.getenv("SOURCE_CACHE_HOURS", "1")) * 3600,
                blocked_domains=tuple(split_csv(os.getenv("BLOCKED_DOMAINS", ""))),
                official_domains=tuple(split_csv(os.getenv("OFFICIAL_DOMAINS", ""))),
                contact=os.getenv("CRAWLER_CONTACT", ""),
            )
        except ValueError as exc:
            raise ConfigurationError("Invalid numeric setting in environment.") from exc
        if result.search_provider not in {"auto", "brave", "tavily"}:
            raise ConfigurationError("SEARCH_PROVIDER must be auto, brave, or tavily.")
        if not all(
            math.isfinite(value)
            for value in [
                result.request_interval,
                result.timeout,
                result.search_cache_seconds,
                result.source_cache_seconds,
            ]
        ):
            raise ConfigurationError("Intervals, timeout and cache lifetimes must be finite numbers.")
        if min(result.request_interval, result.search_cache_seconds, result.source_cache_seconds) < 0:
            raise ConfigurationError("Intervals and cache lifetimes cannot be negative.")
        if not 0 < result.timeout <= 300:
            raise ConfigurationError("HTTP_TIMEOUT_SECONDS must be between 0 and 300.")
        return result
