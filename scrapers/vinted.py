"""Scraper de Vinted vía su endpoint JSON interno de catálogo.

Vinted tampoco tiene API pública oficial. La web necesita una cookie de
sesión anónima (la misma que recibe cualquier visitante sin cuenta al abrir
la home) antes de que el endpoint `/api/v2/catalog/items` responda; sin ella
devuelve 401/403. Este scraper hace un GET a la home para recogerla y, si en
algún momento caduca a mitad de ciclo, vuelve a hacerlo una vez antes de
rendirse. No hay login ni suplantación de usuario: es tráfico anónimo
equivalente al de cualquier visitante.

Si Vinted cambia el endpoint o el nombre de los campos, lo primero es
re-inspeccionar la petición real en devtools y actualizar este módulo.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import Any
from urllib.parse import quote

import httpx

from config import SearchConfig
from models import Listing
from scrapers.base import BaseScraper, RateLimitedError, RETRYABLE_STATUS_CODES, ScraperError

logger = logging.getLogger(__name__)

BASE_URL = "https://www.vinted.es"
SEARCH_URL = f"{BASE_URL}/api/v2/catalog/items"
USER_URL = BASE_URL + "/api/v2/users/{user_id}"

_AUTH_STATUS_CODES = {401, 403}


def _dig(data: dict, *paths: str) -> Any | None:
    """Prueba varias rutas con puntos y devuelve la primera que exista.

    Un segmento numérico indexa listas además de claves de diccionario.
    """
    for path in paths:
        node: Any = data
        found = True
        for key in path.split("."):
            if isinstance(node, dict) and key in node:
                node = node[key]
            elif isinstance(node, list) and key.isdigit() and int(key) < len(node):
                node = node[int(key)]
            else:
                found = False
                break
        if found and node is not None:
            return node
    return None


class VintedScraper(BaseScraper):
    platform = "vinted"

    def __init__(self, scraping_config: Any) -> None:
        super().__init__(scraping_config)
        self._bootstrapped = False

    def _ensure_session(self) -> None:
        if self._bootstrapped:
            return
        self.request("GET", f"{BASE_URL}/")
        self._bootstrapped = True

    def search(self, query: str, search_config: SearchConfig) -> list[Listing]:
        self._ensure_session()

        params = {
            "search_text": query,
            "per_page": search_config.max_results,
            "order": "newest_first",
        }
        # El endpoint devuelve 403 sin un Referer de página real; Accept y
        # Accept-Language ya van por defecto en el cliente (scrapers/base.py).
        headers = {"Referer": f"{BASE_URL}/catalog?search_text={quote(query)}"}
        raw = self._fetch_catalog(params, headers)

        items = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            logger.warning("Vinted: respuesta sin 'items' o con forma inesperada")
            return []

        listings: list[Listing] = []
        for item in items[: search_config.max_results]:
            if not isinstance(item, dict):
                continue
            listing = self._parse_item(item)
            if listing is None:
                continue
            if self.config.fetch_seller_details:
                listing = self._enrich_with_seller(listing, item)
            listings.append(listing)
        return listings

    def _fetch_catalog(self, params: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
        """Como BaseScraper.get_json, pero re-hace la sesión una vez ante 401/403."""
        attempt = 0
        rebootstrapped = False
        while True:
            response = self.raw_request("GET", SEARCH_URL, params=params, headers=headers)

            if response.status_code in _AUTH_STATUS_CODES and not rebootstrapped:
                logger.info(
                    "Vinted: sesión caducada (%d), rehaciendo cookie anónima", response.status_code
                )
                self._bootstrapped = False
                self._ensure_session()
                rebootstrapped = True
                continue

            if response.status_code in RETRYABLE_STATUS_CODES:
                attempt += 1
                if attempt > self.config.max_retries:
                    raise RateLimitedError(
                        f"{SEARCH_URL} devolvió {response.status_code} tras {attempt} intentos"
                    )
                delay = self._backoff_delay(attempt, response.headers.get("Retry-After"))
                logger.warning(
                    "Vinted: %s devolvió %d (intento %d/%d). Reintentando en %.1fs",
                    SEARCH_URL, response.status_code, attempt, self.config.max_retries, delay,
                )
                time.sleep(delay)
                continue

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ScraperError(f"{SEARCH_URL} devolvió {response.status_code}: {exc}") from exc

            try:
                return response.json()
            except ValueError as exc:
                raise ScraperError(f"{SEARCH_URL} no devolvió JSON válido: {exc}") from exc

    def _parse_item(self, item: dict) -> Listing | None:
        item_id = item.get("id")
        title = item.get("title")
        url = item.get("url")

        if not item_id or not title or not url:
            logger.debug("Vinted: item sin id/title/url, se descarta: %r", item)
            return None

        price_amount = _dig(item, "price.amount", "price")
        currency = _dig(item, "price.currency_code", "currency") or "EUR"
        if price_amount is None:
            logger.debug("Vinted: item %s sin precio, se descarta", item_id)
            return None

        image_url = _dig(item, "photo.url", "photo.full_size_url")
        condition = item.get("status")

        return Listing(
            id=str(item_id),
            title=str(title),
            price=float(price_amount),
            currency=str(currency),
            url=str(url),
            seller_rating=None,
            seller_review_count=None,
            image_url=image_url,
            platform=self.platform,
            condition=str(condition) if condition else None,
        )

    def _enrich_with_seller(self, listing: Listing, item: dict) -> Listing:
        seller_id = _dig(item, "user.id", "user_id")
        if not seller_id:
            return listing
        seller_id = str(seller_id)

        cached = self.cached_seller(seller_id)
        if cached is None:
            try:
                cached = self.get_json(USER_URL.format(user_id=seller_id))
            except ScraperError as exc:
                logger.warning("Vinted: no se pudo obtener el vendedor %s: %s", seller_id, exc)
                cached = {}
            self.cache_seller(seller_id, cached)

        # feedback_reputation viene en escala 0-1; el resto del proyecto usa 0-5.
        reputation = _dig(cached, "user.feedback_reputation", "feedback_reputation")
        review_count = _dig(cached, "user.feedback_count", "feedback_count")

        if reputation is None:
            return listing

        return replace(
            listing,
            seller_rating=round(float(reputation) * 5, 2),
            seller_review_count=int(review_count) if review_count is not None else None,
        )
