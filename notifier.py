"""Cliente de la Bot API de Telegram: envío de notificaciones y lectura de los
comandos que llegan al chat (`getUpdates`, ver commands.py).

Un fallo de envío se loguea y no propaga: no debe tumbar el ciclo ni marcar
el anuncio como notificado (eso lo decide quien llame, en main.py).
"""

from __future__ import annotations

import html
import logging
import time

import httpx

from models import Listing

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org/bot{token}/{method}"
CAPTION_LIMIT = 1024
REQUEST_TIMEOUT = 15.0

# Telegram limita el envío a ~1 mensaje/segundo por chat; por encima de eso
# responde 429 ("Too Many Requests: retry after N"). Cuando un ciclo tiene
# varios anuncios nuevos que notificar, sin este throttle+reintento solo el
# primero se enviaba y el resto se perdía (fallaba, no quedaba marcado como
# notificado y el siguiente ciclo repetía el mismo cuello de botella).
MIN_SEND_INTERVAL_SECONDS = 1.1
MAX_FLOOD_RETRIES = 5
DEFAULT_FLOOD_WAIT_SECONDS = 2.0
MAX_FLOOD_WAIT_SECONDS = 60.0


class TelegramNotifier:
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        min_send_interval_seconds: float = MIN_SEND_INTERVAL_SECONDS,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._min_send_interval_seconds = min_send_interval_seconds
        self._last_sent_at: float | None = None

    @property
    def chat_id(self) -> str:
        """Chat autorizado. commands.py lo usa para ignorar mensajes de terceros."""
        return self._chat_id

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

    def send_text(self, text: str) -> bool:
        """Envía un mensaje suelto (respuestas a comandos). No lanza excepciones."""
        return self._send_message(text)

    def get_updates(self, offset: int | None = None, limit: int = 100) -> list[dict] | None:
        """Mensajes pendientes dirigidos al bot, o None si la consulta falló.

        Sin long polling (`timeout=0`): el bot no es un proceso persistente,
        cada ciclo consulta lo que haya pendiente y sale. Telegram descarta
        los updates anteriores a `offset`, así que pasar el último visto + 1
        es lo que evita reprocesar comandos ya atendidos.
        """
        payload: dict[str, object] = {
            "timeout": 0,
            "limit": limit,
            # Un comando corregido a mano (editar el mensaje) también vale.
            "allowed_updates": ["message", "edited_message"],
        }
        if offset is not None:
            payload["offset"] = offset

        data = self._call("getUpdates", payload)
        if data is None:
            return None

        result = data.get("result")
        if not isinstance(result, list):
            logger.error("Telegram: getUpdates devolvió un 'result' inesperado: %r", result)
            return None
        return [update for update in result if isinstance(update, dict)]

    def _send_message(self, text: str) -> bool:
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        return self._post("sendMessage", payload)

    def _post(self, method: str, payload: dict) -> bool:
        return self._call(method, payload) is not None

    def _call(self, method: str, payload: dict) -> dict | None:
        """Llama a la Bot API. Devuelve el JSON de respuesta, o None si falló."""
        url = API_BASE.format(token=self._bot_token, method=method)
        attempt = 0
        while True:
            self._throttle()
            try:
                response = httpx.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            except httpx.HTTPError as exc:
                logger.error("Telegram: fallo de red/HTTP en %s: %s", method, exc)
                return None

            if response.status_code == 429 and attempt < MAX_FLOOD_RETRIES:
                attempt += 1
                wait_seconds = self._flood_wait_seconds(response)
                logger.warning(
                    "Telegram: %s limitado por control de flood (intento %d/%d), reintentando en %.1fs",
                    method, attempt, MAX_FLOOD_RETRIES, wait_seconds,
                )
                time.sleep(wait_seconds)
                continue

            try:
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPError as exc:
                logger.error("Telegram: fallo de red/HTTP en %s: %s", method, exc)
                return None
            except ValueError as exc:
                logger.error("Telegram: %s no devolvió JSON válido: %s", method, exc)
                return None

            if not isinstance(data, dict) or not data.get("ok"):
                logger.error("Telegram: %s respondió sin éxito: %s", method, data)
                return None
            return data

    def _throttle(self) -> None:
        if self._last_sent_at is not None:
            elapsed = time.monotonic() - self._last_sent_at
            remaining = self._min_send_interval_seconds - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_sent_at = time.monotonic()

    def _flood_wait_seconds(self, response: httpx.Response) -> float:
        try:
            retry_after = response.json().get("parameters", {}).get("retry_after")
            if retry_after is not None:
                return min(float(retry_after), MAX_FLOOD_WAIT_SECONDS)
        except ValueError:
            pass
        header = response.headers.get("Retry-After")
        if header is not None:
            try:
                return min(float(header), MAX_FLOOD_WAIT_SECONDS)
            except ValueError:
                pass
        return DEFAULT_FLOOD_WAIT_SECONDS
