"""Comandos de Telegram para cambiar qué se vigila sin tocar el código.

El bot no es un proceso persistente (ver CLAUDE.md): cada ciclo de main.py
consulta con `getUpdates` los mensajes pendientes, aplica los comandos que
encuentre, guarda el resultado en SQLite y sigue con el scrapeo. Consecuencias
prácticas:

- Un comando enviado entre dos ciclos no se atiende al instante: se aplica —y
  se contesta— al arrancar el ciclo siguiente (con el cron actual de GitHub
  Actions, hasta 30 minutos después).
- El comando afecta ya al ciclo en el que se lee, no al siguiente.
- Los ajustes viven en la tabla `settings` de la BD, que es el único estado
  que sobrevive entre ejecuciones en todos los despliegues. config.yaml queda
  intacto: es el valor por defecto al que se vuelve con /reset.
- Solo se atienden mensajes del chat configurado en TELEGRAM_CHAT_ID; lo que
  llegue de cualquier otro se ignora (el token es público de facto: cualquiera
  que lo tenga puede escribirle al bot).
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass, replace

from config import AppConfig, ConfigError, SearchConfig, apply_search_overrides
from notifier import TelegramNotifier
from storage import Storage

logger = logging.getLogger(__name__)

OFFSET_KEY = "telegram.updates_offset"
OVERRIDE_PREFIX = "override."

# Prefijo con el que config.py etiqueta los errores de validación de un ajuste;
# se recorta al contestar por el chat (ver _humanize).
_CONTEXT = "ajuste"

# Campos numéricos que se guardan como texto en `settings` y hay que
# reconvertir al leerlos.
_NUMERIC_OVERRIDES = {"price_threshold", "min_price"}

# Para que un error de validación se lea como un mensaje de chat y no como un
# volcado de config.yaml.
_FIELD_LABELS = {
    "query": "el producto",
    "price_threshold": "el precio máximo",
    "min_price": "el precio mínimo",
}

HELP_TEXT = (
    "<b>SnipeBot</b> — comandos disponibles\n\n"
    "/producto <i>texto</i> — qué buscar (ej. <code>/producto xbox series x</code>)\n"
    "/precio <i>n</i> — precio máximo en € (ej. <code>/precio 600</code>)\n"
    "/preciomin <i>n</i> — precio mínimo en € (ej. <code>/preciomin 0</code>)\n"
    "/estado — qué se está vigilando ahora mismo\n"
    "/reset — vuelve a los valores de config.yaml\n"
    "/ayuda — este mensaje\n\n"
    "Los cambios se aplican en el ciclo en que se leen y se mantienen entre "
    "ejecuciones. El bot solo mira el chat cada vez que corre un ciclo, así "
    "que puede tardar en contestar."
)


@dataclass(frozen=True, slots=True)
class Command:
    name: str
    args: str


def parse_command(text: str) -> Command | None:
    """Convierte un mensaje en un Command, o None si no es un comando."""
    text = text.strip()
    if not text.startswith("/"):
        return None
    head, _, rest = text.partition(" ")
    # En grupos, Telegram entrega "/precio@MiBot 600".
    name = head[1:].split("@", 1)[0].strip().lower()
    if not name:
        return None
    return Command(name=name, args=rest.strip())


def load_overrides(storage: Storage) -> dict[str, object]:
    """Ajustes guardados, ya convertidos al tipo que espera SearchConfig."""
    overrides: dict[str, object] = {}
    for key, raw_value in storage.get_settings(OVERRIDE_PREFIX).items():
        field_name = key[len(OVERRIDE_PREFIX) :]
        if field_name in _NUMERIC_OVERRIDES:
            try:
                overrides[field_name] = float(raw_value)
            except ValueError:
                logger.error(
                    "Ajuste '%s' guardado con un valor no numérico (%r); se ignora",
                    key, raw_value,
                )
                continue
        else:
            overrides[field_name] = raw_value
    return overrides


def apply_overrides(config: AppConfig, overrides: dict[str, object]) -> AppConfig:
    """Devuelve la config con los ajustes de Telegram aplicados a cada búsqueda.

    Un ajuste inválido para una búsqueda concreta (por ejemplo un precio
    mínimo por encima del máximo de esa plataforma) se descarta para ella y se
    loguea: es preferible seguir vigilando con lo que había a no vigilar nada.
    """
    if not overrides:
        return config

    searches: list[SearchConfig] = []
    for search in config.searches:
        try:
            searches.append(apply_search_overrides(search, overrides))
        except ConfigError as exc:
            logger.error(
                "Ajustes de Telegram inválidos para %s:%r, se usa config.yaml: %s",
                search.platform, search.query, exc,
            )
            searches.append(search)
    return replace(config, searches=searches)


def describe(config: AppConfig, overrides: dict[str, object]) -> str:
    """Resumen de lo que se está vigilando, para contestar a los comandos."""
    effective = apply_overrides(config, overrides)
    lines = ["<b>Vigilando ahora</b>"]

    for search in effective.searches:
        query = _esc(search.query)
        lines.append(f"\n• <b>{query}</b> en {_esc(search.platform)}")
        lines.append(f"  Precio: {_money(search.min_price)} – {_money(search.price_threshold)}")
        rating = f"  Valoración mínima: {search.seller_rating_threshold}"
        if search.min_seller_reviews:
            rating += f" (con ≥ {search.min_seller_reviews} valoraciones)"
        lines.append(rating)
        if search.min_condition:
            lines.append(f"  Estado mínimo: {_esc(search.min_condition)}")

    if overrides:
        cambiados = sorted(_FIELD_LABELS.get(name, name) for name in overrides)
        lines.append(
            f"\nCambiado desde Telegram: {', '.join(cambiados)}. /reset vuelve a config.yaml."
        )
    else:
        lines.append("\nTodo según config.yaml.")

    return "\n".join(lines)


def handle_command(command: Command, config: AppConfig, storage: Storage) -> str | None:
    """Ejecuta un comando y devuelve la respuesta, o None si no hay que contestar."""
    if command.name in {"start", "ayuda", "help"}:
        return HELP_TEXT

    if command.name in {"producto", "product"}:
        if not command.args:
            return "Uso: <code>/producto xbox series x</code>"
        return _set_override(config, storage, "query", command.args, "Producto")

    if command.name in {"precio", "preciomax", "preciomaximo"}:
        value = _parse_price(command.args)
        if value is None:
            return "Uso: <code>/precio 600</code> (precio máximo en €)"
        return _set_override(config, storage, "price_threshold", value, "Precio máximo")

    if command.name in {"preciomin", "preciominimo"}:
        value = _parse_price(command.args)
        if value is None:
            return "Uso: <code>/preciomin 250</code> (precio mínimo en €)"
        return _set_override(config, storage, "min_price", value, "Precio mínimo")

    if command.name in {"estado", "status", "config"}:
        return describe(config, load_overrides(storage))

    if command.name in {"reset", "reiniciar"}:
        storage.delete_settings(OVERRIDE_PREFIX)
        return "↩️ Ajustes borrados, se vuelve a config.yaml.\n\n" + describe(config, {})

    return f"No conozco el comando /{_esc(command.name)}.\n\n{HELP_TEXT}"


def process_updates(notifier: TelegramNotifier, config: AppConfig, storage: Storage) -> int:
    """Lee y ejecuta los comandos pendientes. Devuelve cuántos atendió.

    No propaga errores: si Telegram falla, el ciclo debe seguir vigilando con
    los ajustes que ya tenía guardados.
    """
    stored_offset = storage.get_setting(OFFSET_KEY)
    try:
        offset = int(stored_offset) if stored_offset is not None else None
    except ValueError:
        logger.error("Offset de getUpdates corrupto (%r); se reinicia", stored_offset)
        offset = None

    updates = notifier.get_updates(offset)
    if updates is None:
        logger.warning("No se pudieron leer los comandos de Telegram; se sigue con los ajustes guardados")
        return 0

    handled = 0
    max_update_id: int | None = None

    for update in updates:
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            max_update_id = update_id if max_update_id is None else max(max_update_id, update_id)

        try:
            reply = _handle_update(update, notifier, config, storage)
        except Exception:
            # Se marca igualmente como procesado (más abajo) para no quedarse
            # atascado reintentando el mismo mensaje en cada ciclo.
            logger.exception("Error procesando un update de Telegram; se descarta")
            continue

        if reply is not None:
            handled += 1
            notifier.send_text(reply)

    if max_update_id is not None:
        storage.set_setting(OFFSET_KEY, str(max_update_id + 1))

    if handled:
        logger.info("Comandos de Telegram atendidos: %d", handled)
    return handled


def _handle_update(
    update: dict,
    notifier: TelegramNotifier,
    config: AppConfig,
    storage: Storage,
) -> str | None:
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return None

    chat = message.get("chat")
    chat_id = str(chat.get("id")) if isinstance(chat, dict) else ""
    if chat_id != str(notifier.chat_id):
        logger.warning("Mensaje ignorado: viene del chat %s, no del autorizado", chat_id or "?")
        return None

    text = message.get("text")
    if not isinstance(text, str):
        return None

    command = parse_command(text)
    if command is None:
        return None

    logger.info("Comando recibido: /%s %s", command.name, command.args)
    return handle_command(command, config, storage)


def _set_override(
    config: AppConfig,
    storage: Storage,
    field_name: str,
    value: object,
    label: str,
) -> str:
    """Valida el ajuste contra todas las búsquedas y, si vale, lo guarda."""
    pending = load_overrides(storage) | {field_name: value}

    for search in config.searches:
        try:
            apply_search_overrides(search, pending, context=_CONTEXT)
        except ConfigError as exc:
            return (
                f"❌ No se puede aplicar: {_humanize(str(exc))}\n\n"
                "/estado te dice los valores que hay ahora."
            )

    storage.set_setting(OVERRIDE_PREFIX + field_name, _serialize(value))
    shown = _esc(str(value)) if field_name == "query" else _money(float(value))
    return f"✅ {label}: {shown}\n\n{describe(config, pending)}"


def _serialize(value: object) -> str:
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _humanize(message: str) -> str:
    """Traduce un ConfigError a algo legible en un chat."""
    message = message.removeprefix(f"{_CONTEXT}: ")
    for field_name, label in _FIELD_LABELS.items():
        message = message.replace(f"'{field_name}'", label)
    return _esc(message)


def _esc(text: str) -> str:
    """Escapa para parse_mode HTML.

    Sin `quote=True`: Telegram solo documenta `&lt;`, `&gt;` y `&amp;`, así que
    convertir las comillas a entidades numéricas puede acabar mostrándolas
    literalmente. Fuera de atributos HTML no hace falta escaparlas.
    """
    return html.escape(text, quote=False)


def _parse_price(text: str) -> float | None:
    """Acepta '600', '600€', '600,50', '1.200' y demás formas de escribir un precio.

    El punto es ambiguo escribiendo en español: en "1.200" separa miles y en
    "600.50" separa decimales. Se resuelve como lo haría cualquiera al leerlo:
    si hay coma, ella manda y los puntos son de miles; si no, un punto seguido
    de exactamente tres dígitos es separador de miles.
    """
    cleaned = text.strip().lower().replace("€", "").replace("eur", "").replace(" ", "")
    if not cleaned:
        return None

    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "." in cleaned and len(cleaned.rsplit(".", 1)[1]) == 3:
        cleaned = cleaned.replace(".", "")

    try:
        return float(cleaned)
    except ValueError:
        return None


def _money(value: float) -> str:
    return f"{value:,.2f} €".replace(",", "@").replace(".", ",").replace("@", ".")
