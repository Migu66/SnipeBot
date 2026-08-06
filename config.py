"""Carga y validación de configuración: config.yaml (público) + .env (secretos)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

from models import CONDITION_RANK

SUPPORTED_PLATFORMS = {"wallapop", "vinted"}

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class ConfigError(Exception):
    """Configuración inválida o incompleta. El mensaje debe decir qué campo falla."""


@dataclass(frozen=True, slots=True)
class SearchConfig:
    platform: str
    query: str
    price_threshold: float
    seller_rating_threshold: float
    min_price: float = 0.0
    min_seller_reviews: int = 0
    allow_unknown_rating: bool = False
    min_condition: str | None = None
    max_results: int = 40


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    bot_token: str
    chat_id: str


@dataclass(frozen=True, slots=True)
class ScrapingConfig:
    request_delay_seconds: float = 3.0
    timeout_seconds: float = 20.0
    max_retries: int = 3
    backoff_base_seconds: float = 5.0
    fetch_seller_details: bool = True
    user_agent: str = DEFAULT_USER_AGENT
    wallapop_latitude: float = 40.4168
    wallapop_longitude: float = -3.7038


@dataclass(frozen=True, slots=True)
class AppConfig:
    searches: list[SearchConfig] = field(default_factory=list)
    telegram: TelegramConfig | None = None
    scraping: ScrapingConfig = field(default_factory=ScrapingConfig)
    database_path: str = "data/snipebot.db"
    log_level: str = "INFO"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigError(message)


def _as_number(value: object, field_name: str, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{context}: '{field_name}' debe ser numérico, recibido {value!r}")
    return float(value)


def _parse_search(raw: dict, index: int) -> SearchConfig:
    context = f"searches[{index}]"
    _require(isinstance(raw, dict), f"{context}: debe ser un objeto, recibido {raw!r}")

    platform = raw.get("platform")
    _require(
        platform in SUPPORTED_PLATFORMS,
        f"{context}: 'platform' debe ser una de {sorted(SUPPORTED_PLATFORMS)}, recibido {platform!r}",
    )

    query = raw.get("query")
    _require(
        isinstance(query, str) and query.strip(),
        f"{context}: 'query' debe ser una cadena no vacía",
    )

    price_threshold = _as_number(raw.get("price_threshold"), "price_threshold", context)
    _require(price_threshold > 0, f"{context}: 'price_threshold' debe ser > 0, recibido {price_threshold}")

    seller_rating_threshold = _as_number(
        raw.get("seller_rating_threshold"), "seller_rating_threshold", context
    )
    _require(
        0 <= seller_rating_threshold <= 5,
        f"{context}: 'seller_rating_threshold' debe estar entre 0 y 5, recibido {seller_rating_threshold}",
    )

    min_price = _as_number(raw.get("min_price", 0.0), "min_price", context)
    _require(min_price >= 0, f"{context}: 'min_price' debe ser >= 0, recibido {min_price}")
    _require(
        min_price <= price_threshold,
        f"{context}: 'min_price' ({min_price}) no puede ser mayor que 'price_threshold' ({price_threshold})",
    )

    min_seller_reviews = raw.get("min_seller_reviews", 0)
    _require(
        isinstance(min_seller_reviews, int) and not isinstance(min_seller_reviews, bool) and min_seller_reviews >= 0,
        f"{context}: 'min_seller_reviews' debe ser un entero >= 0, recibido {min_seller_reviews!r}",
    )

    allow_unknown_rating = raw.get("allow_unknown_rating", False)
    _require(
        isinstance(allow_unknown_rating, bool),
        f"{context}: 'allow_unknown_rating' debe ser booleano, recibido {allow_unknown_rating!r}",
    )

    max_results = raw.get("max_results", 40)
    _require(
        isinstance(max_results, int) and not isinstance(max_results, bool) and max_results > 0,
        f"{context}: 'max_results' debe ser un entero > 0, recibido {max_results!r}",
    )

    min_condition = raw.get("min_condition")
    if min_condition is not None:
        _require(
            isinstance(min_condition, str) and min_condition.strip(),
            f"{context}: 'min_condition' debe ser una cadena no vacía",
        )
        min_condition = min_condition.strip()
        _require(
            min_condition.lower() in CONDITION_RANK,
            f"{context}: 'min_condition' debe ser una de "
            f"{sorted(CONDITION_RANK, key=CONDITION_RANK.get)}, recibido {min_condition!r}",
        )

    return SearchConfig(
        platform=platform,
        query=query.strip(),
        price_threshold=price_threshold,
        seller_rating_threshold=seller_rating_threshold,
        min_price=min_price,
        min_seller_reviews=min_seller_reviews,
        allow_unknown_rating=allow_unknown_rating,
        min_condition=min_condition,
        max_results=max_results,
    )


def _parse_scraping(raw: dict | None) -> ScrapingConfig:
    raw = raw or {}
    _require(isinstance(raw, dict), f"'scraping' debe ser un objeto, recibido {raw!r}")

    defaults = ScrapingConfig()

    request_delay_seconds = _as_number(
        raw.get("request_delay_seconds", defaults.request_delay_seconds),
        "request_delay_seconds",
        "scraping",
    )
    _require(
        request_delay_seconds >= 2,
        f"scraping: 'request_delay_seconds' debe ser >= 2, recibido {request_delay_seconds} "
        "(CLAUDE.md exige rate limiting real entre peticiones a la misma plataforma)",
    )

    timeout_seconds = _as_number(
        raw.get("timeout_seconds", defaults.timeout_seconds), "timeout_seconds", "scraping"
    )
    _require(timeout_seconds > 0, f"scraping: 'timeout_seconds' debe ser > 0, recibido {timeout_seconds}")

    max_retries = raw.get("max_retries", defaults.max_retries)
    _require(
        isinstance(max_retries, int) and not isinstance(max_retries, bool) and max_retries >= 0,
        f"scraping: 'max_retries' debe ser un entero >= 0, recibido {max_retries!r}",
    )

    backoff_base_seconds = _as_number(
        raw.get("backoff_base_seconds", defaults.backoff_base_seconds),
        "backoff_base_seconds",
        "scraping",
    )
    _require(
        backoff_base_seconds > 0,
        f"scraping: 'backoff_base_seconds' debe ser > 0, recibido {backoff_base_seconds}",
    )

    fetch_seller_details = raw.get("fetch_seller_details", defaults.fetch_seller_details)
    _require(
        isinstance(fetch_seller_details, bool),
        f"scraping: 'fetch_seller_details' debe ser booleano, recibido {fetch_seller_details!r}",
    )

    user_agent = raw.get("user_agent", defaults.user_agent)
    _require(
        isinstance(user_agent, str) and user_agent.strip(),
        "scraping: 'user_agent' debe ser una cadena no vacía",
    )

    wallapop_latitude = _as_number(
        raw.get("wallapop_latitude", defaults.wallapop_latitude), "wallapop_latitude", "scraping"
    )
    wallapop_longitude = _as_number(
        raw.get("wallapop_longitude", defaults.wallapop_longitude), "wallapop_longitude", "scraping"
    )

    return ScrapingConfig(
        request_delay_seconds=request_delay_seconds,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        backoff_base_seconds=backoff_base_seconds,
        fetch_seller_details=fetch_seller_details,
        user_agent=user_agent,
        wallapop_latitude=wallapop_latitude,
        wallapop_longitude=wallapop_longitude,
    )


def _parse_telegram() -> TelegramConfig:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    _require(
        bool(bot_token),
        "falta TELEGRAM_BOT_TOKEN en el entorno/.env (créalo con @BotFather)",
    )
    _require(
        bool(chat_id),
        "falta TELEGRAM_CHAT_ID en el entorno/.env",
    )
    return TelegramConfig(bot_token=bot_token, chat_id=chat_id)


def load_config(path: str | Path = "config.yaml", env_file: str | Path = ".env") -> AppConfig:
    """Lee `config.yaml`, carga `.env` y devuelve una AppConfig validada.

    Lanza ConfigError con un mensaje que dice exactamente qué campo está mal.
    """
    config_path = Path(path)
    _require(config_path.is_file(), f"no se encuentra el fichero de configuración: {config_path}")

    env_path = Path(env_file)
    if env_path.is_file():
        load_dotenv(env_path, override=False)

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{config_path}: YAML inválido: {exc}") from exc

    _require(isinstance(raw, dict), f"{config_path}: el documento raíz debe ser un objeto YAML")

    raw_searches = raw.get("searches")
    _require(
        isinstance(raw_searches, list) and len(raw_searches) > 0,
        "'searches' debe ser una lista no vacía de búsquedas",
    )
    searches = [_parse_search(item, i) for i, item in enumerate(raw_searches)]

    scraping = _parse_scraping(raw.get("scraping"))
    telegram = _parse_telegram()

    database_path = raw.get("database_path", "data/snipebot.db")
    _require(
        isinstance(database_path, str) and database_path.strip(),
        "'database_path' debe ser una cadena no vacía",
    )

    log_level = raw.get("log_level", "INFO")
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR"}
    _require(
        isinstance(log_level, str) and log_level.upper() in valid_levels,
        f"'log_level' debe ser una de {sorted(valid_levels)}, recibido {log_level!r}",
    )

    return AppConfig(
        searches=searches,
        telegram=telegram,
        scraping=scraping,
        database_path=database_path,
        log_level=log_level.upper(),
    )
