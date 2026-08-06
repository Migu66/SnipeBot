"""Utilidades compartidas por los scrapers: rate limiting, reintentos con backoff, sesión HTTP.

Los endpoints de Wallapop y Vinted no son API pública: pueden cambiar sin
aviso. Por eso el parseo en cada scraper concreto es defensivo y este módulo
concentra la política de reintentos para no repetirla.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from config import ScrapingConfig

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 403, 500, 502, 503, 504}


class ScraperError(Exception):
    """Fallo irrecuperable de un scraper para una búsqueda concreta.

    Debe capturarse por búsqueda en main.py para que el resto del ciclo siga.
    """


class RateLimitedError(ScraperError):
    """Se agotaron los reintentos ante respuestas 429/403/5xx."""


class RateLimiter:
    """Espera hasta que haya pasado `delay_seconds` desde la última petición a esta plataforma."""

    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds
        self._last_request_at: float | None = None

    def wait(self) -> None:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            remaining = self.delay_seconds - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at = time.monotonic()


class BaseScraper:
    """Cliente HTTP común: rate limiting, backoff exponencial, caché de vendedores por ciclo."""

    platform: str = "base"

    def __init__(self, scraping_config: ScrapingConfig) -> None:
        self.config = scraping_config
        self._rate_limiter = RateLimiter(scraping_config.request_delay_seconds)
        self._client = httpx.Client(
            headers={
                "User-Agent": scraping_config.user_agent,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            },
            timeout=scraping_config.timeout_seconds,
            follow_redirects=True,
        )
        self._seller_cache: dict[str, Any] = {}

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "BaseScraper":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- caché de vendedores dentro del ciclo -------------------------------

    def cached_seller(self, seller_id: str) -> Any | None:
        return self._seller_cache.get(seller_id)

    def cache_seller(self, seller_id: str, value: Any) -> None:
        self._seller_cache[seller_id] = value

    # -- HTTP con backoff -----------------------------------------------------

    def raw_request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Un único intento (con rate limiting, sin backoff/reintentos).

        Pensado para scrapers que necesitan inspeccionar el status code antes
        de decidir si reintentar, re-autenticar, etc.
        """
        self._rate_limiter.wait()
        return self._client.request(method, url, params=params, headers=headers)

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Petición con rate limiting y backoff exponencial ante 429/403/5xx/errores de red."""
        attempt = 0
        while True:
            self._rate_limiter.wait()
            try:
                response = self._client.request(method, url, params=params, headers=headers)
            except httpx.TransportError as exc:
                attempt += 1
                if attempt > self.config.max_retries:
                    raise ScraperError(
                        f"error de red tras {attempt} intentos en {url}: {exc}"
                    ) from exc
                delay = self._backoff_delay(attempt, retry_after=None)
                logger.warning(
                    "Error de red en %s (intento %d/%d): %s. Reintentando en %.1fs",
                    url, attempt, self.config.max_retries, exc, delay,
                )
                time.sleep(delay)
                continue

            if response.status_code in RETRYABLE_STATUS_CODES:
                attempt += 1
                if attempt > self.config.max_retries:
                    raise RateLimitedError(
                        f"{url} devolvió {response.status_code} tras {attempt} intentos"
                    )
                delay = self._backoff_delay(attempt, retry_after=response.headers.get("Retry-After"))
                logger.warning(
                    "%s devolvió %d (intento %d/%d). Reintentando en %.1fs",
                    url, response.status_code, attempt, self.config.max_retries, delay,
                )
                time.sleep(delay)
                continue

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ScraperError(f"{url} devolvió {response.status_code}: {exc}") from exc

            return response

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        response = self.request("GET", url, params=params, headers=headers)
        try:
            return response.json()
        except ValueError as exc:
            raise ScraperError(f"{url} no devolvió JSON válido: {exc}") from exc

    def _backoff_delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after is not None:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return self.config.backoff_base_seconds * (2 ** (attempt - 1))

    # -- interfaz de scraper concreto -----------------------------------------

    def search(self, query: str, search_config: Any) -> list[Any]:
        raise NotImplementedError
