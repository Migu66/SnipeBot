"""Scraper de Wallapop vía su endpoint JSON interno de búsqueda.

Wallapop no tiene API pública oficial. `SEARCH_URL` es la ruta real usada
por el frontend actual (`api/v3/search/section`, localizada inspeccionando
los bundles de Next.js de es.wallapop.com) — la ruta antigua `api/v3/search`
que se usaba aquí antes está deprecada y devuelve 400 siempre.

ESTADO CONOCIDO (sin resolver): con los parámetros documentados en el propio
JS del frontend (`keywords`, `latitude`, `longitude`, `order_by`, `source`)
y la cabecera `X-DeviceOS` que exige el WAF, el endpoint sigue devolviendo
`400 {"status":400,"message":"","errors":[]}` sin más detalle. Se confirmó
que es GET (un POST da 405 explícito) y que no es un bloqueo de Cloudflare
(un 403 de CloudFront aparece solo si falta `X-DeviceOS`, no es esto). Falta
identificar qué parámetro adicional exige. Un navegador headless real
(Chrome vía CDP) fue bloqueado con 403 antes de ejecutar ningún JS —
probablemente fingerprinting anti-bot — así que no se intentó forzarlo por
ahí (evadir esa protección está fuera de alcance de este proyecto). El
siguiente paso realista es capturar la petición real desde un navegador con
sesión humana normal (DevTools -> Network -> XHR) y comparar contra lo de
aquí.

Si Wallapop cambia el endpoint otra vez, lo primero es re-inspeccionar la
petición real y actualizar `SEARCH_URL` / los nombres de campo de aquí. La
forma de la respuesta esperada (una vez funcione) es tolerante a varias
formas conocidas: `_extract_items` acepta tanto `{"search_objects": [...]}`
como `{"data": {"section": {"payload": {"items": [...]}}}}`.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any
from urllib.parse import quote

from config import SearchConfig
from models import Listing
from scrapers.base import BaseScraper, ScraperError

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.wallapop.com/api/v3/search/section"
USER_URL = "https://api.wallapop.com/api/v3/users/{user_id}"
ITEM_URL_TEMPLATE = "https://es.wallapop.com/item/{web_slug}"


def _dig(data: dict, *paths: str) -> Any | None:
    """Prueba varias rutas con puntos (p.ej. 'images.0.urls.big') y devuelve la primera que exista.

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


class WallapopScraper(BaseScraper):
    platform = "wallapop"

    def search(self, query: str, search_config: SearchConfig) -> list[Listing]:
        params = {
            "keywords": query,
            "latitude": self.config.wallapop_latitude,
            "longitude": self.config.wallapop_longitude,
            "order_by": "newest",
            "source": "search_box",
        }
        # X-DeviceOS es obligatorio (el WAF de CloudFront devuelve 403 sin
        # ella). Con ella se llega al backend real, que de momento devuelve
        # 400 igualmente — ver nota de estado al principio del fichero.
        headers = {
            "Referer": f"https://es.wallapop.com/app/search?keywords={quote(query)}",
            "X-DeviceOS": "0",
        }

        raw = self.get_json(SEARCH_URL, params=params, headers=headers)

        items = self._extract_items(raw)
        listings: list[Listing] = []
        for item in items[: search_config.max_results]:
            listing = self._parse_item(item)
            if listing is None:
                continue
            if self.config.fetch_seller_details:
                listing = self._enrich_with_seller(listing, item)
            listings.append(listing)
        return listings

    def _extract_items(self, raw: Any) -> list[dict]:
        if not isinstance(raw, dict):
            logger.warning("Wallapop: respuesta inesperada (no es un objeto): %r", type(raw))
            return []

        # Forma antigua conocida: {"search_objects": [...]}
        if isinstance(raw.get("search_objects"), list):
            return [item for item in raw["search_objects"] if isinstance(item, dict)]

        # Forma anidada más reciente: data.section.payload.items[].content
        payload_items = _dig(raw, "data.section.payload.items")
        if isinstance(payload_items, list):
            items = []
            for entry in payload_items:
                if not isinstance(entry, dict):
                    continue
                items.append(entry.get("content", entry))
            return items

        logger.warning("Wallapop: no se reconoce la forma de la respuesta, claves: %s", list(raw.keys()))
        return []

    def _parse_item(self, item: dict) -> Listing | None:
        item_id = _dig(item, "id", "itemId")
        title = _dig(item, "title", "name")
        web_slug = _dig(item, "web_slug", "webSlug")

        if not item_id or not title or not web_slug:
            logger.debug("Wallapop: item sin id/title/web_slug, se descarta: %r", item)
            return None

        price_amount = _dig(item, "price.amount", "price")
        currency = _dig(item, "price.currency", "currency") or "EUR"
        if price_amount is None:
            logger.debug("Wallapop: item %s sin precio, se descarta", item_id)
            return None

        image_url = _dig(
            item,
            "images.0.urls.big",
            "images.0.urls.original",
            "images.0.original",
            "images.0.url",
        )
        if image_url is None:
            images = item.get("images")
            if isinstance(images, list) and images and isinstance(images[0], str):
                image_url = images[0]

        return Listing(
            id=str(item_id),
            title=str(title),
            price=float(price_amount),
            currency=str(currency),
            url=ITEM_URL_TEMPLATE.format(web_slug=web_slug),
            seller_rating=None,
            seller_review_count=None,
            image_url=image_url,
            platform=self.platform,
        )

    def _enrich_with_seller(self, listing: Listing, item: dict) -> Listing:
        seller_id = _dig(item, "user.id", "seller.id")
        if not seller_id:
            return listing
        seller_id = str(seller_id)

        cached = self.cached_seller(seller_id)
        if cached is None:
            try:
                cached = self.get_json(USER_URL.format(user_id=seller_id))
            except ScraperError as exc:
                logger.warning("Wallapop: no se pudo obtener el vendedor %s: %s", seller_id, exc)
                cached = {}
            self.cache_seller(seller_id, cached)

        rating = _dig(cached, "rating", "reputation.rating", "stats.rating")
        review_count = _dig(
            cached, "reputation.totalReviews", "reputation.review_count", "numberOfRatings"
        )

        if rating is None:
            return listing

        return replace(
            listing,
            seller_rating=float(rating),
            seller_review_count=int(review_count) if review_count is not None else None,
        )
