"""Envío de notificaciones a Telegram vía Bot API.

Un fallo de envío se loguea y no propaga: no debe tumbar el ciclo ni marcar
el anuncio como notificado (eso lo decide quien llame, en main.py).
"""

from __future__ import annotations

import html
import logging

import httpx

from models import Listing

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org/bot{token}/{method}"
CAPTION_LIMIT = 1024
REQUEST_TIMEOUT = 15.0


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id

    def send_listing(self, listing: Listing) -> bool:
        """Envía un anuncio. Nunca lanza excepciones: devuelve False si falla."""
        message = self._build_message(listing)

        if listing.image_url:
            if self._send_photo(listing.image_url, message):
                return True
            logger.warning(
                "Telegram: sendPhoto falló para %s/%s, probando sendMessage sin imagen",
                listing.platform, listing.id,
            )

        return self._send_message(message)

    def _build_message(self, listing: Listing) -> str:
        title = html.escape(listing.title)
        platform = html.escape(listing.platform)
        rating = html.escape(listing.rating_label())
        price = html.escape(listing.price_label())
        url = html.escape(listing.url)
        return f"<b>{title}</b>\n{price} · {platform}\nVendedor: {rating}\n{url}"

    def _send_photo(self, image_url: str, caption: str) -> bool:
        if len(caption) > CAPTION_LIMIT:
            caption = caption[: CAPTION_LIMIT - 1] + "…"
        payload = {
            "chat_id": self._chat_id,
            "photo": image_url,
            "caption": caption,
            "parse_mode": "HTML",
        }
        return self._post("sendPhoto", payload)

    def _send_message(self, text: str) -> bool:
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        return self._post("sendMessage", payload)

    def _post(self, method: str, payload: dict) -> bool:
        url = API_BASE.format(token=self._bot_token, method=method)
        try:
            response = httpx.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            logger.error("Telegram: fallo de red/HTTP en %s: %s", method, exc)
            return False
        except ValueError as exc:
            logger.error("Telegram: %s no devolvió JSON válido: %s", method, exc)
            return False

        if not data.get("ok"):
            logger.error("Telegram: %s respondió sin éxito: %s", method, data)
            return False
        return True
