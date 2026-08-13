"""Punto de entrada único. Pensado para ser invocado por cron / un timer, no
como proceso persistente: lee comandos de Telegram -> scrape -> filtra ->
deduplica -> notifica, y sale.

Códigos de salida:
  0 - ok (aunque no haya habido resultados nuevos)
  1 - error de configuración, no se llegó a ejecutar ningún ciclo
  2 - todas las búsquedas fallaron
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from pathlib import Path
from types import TracebackType

import commands
from config import AppConfig, ConfigError, ScrapingConfig, SearchConfig, load_config
from filters import apply_filters
from models import Listing
from notifier import TelegramNotifier
from scrapers import ScraperError, get_scraper
from storage import Storage

logger = logging.getLogger(__name__)

DEFAULT_LOCK_STALE_SECONDS = 15 * 60


class LockError(Exception):
    """Ya hay otro ciclo en marcha (o el lock está bloqueado por otra razón)."""


class FileLock:
    """Lock de fichero simple con detección de lock obsoleto por antigüedad.

    Dos ejecuciones de cron no deben solaparse; si una anterior murió sin
    limpiar el lock, uno con más de `stale_after_seconds` de antigüedad se
    considera abandonado y se sobrescribe.
    """

    def __init__(self, path: Path, stale_after_seconds: float = DEFAULT_LOCK_STALE_SECONDS) -> None:
        self.path = path
        self.stale_after_seconds = stale_after_seconds
        self._acquired = False

    def acquire(self) -> None:
        if self.path.exists():
            age = time.time() - self.path.stat().st_mtime
            if age < self.stale_after_seconds:
                raise LockError(
                    f"ya hay un ciclo en marcha: {self.path} tiene {age:.0f}s de antigüedad"
                )
            logger.warning("Lock %s obsoleto (%.0fs), se descarta y se sobrescribe", self.path, age)
            self.path.unlink()

        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise LockError(f"ya hay un ciclo en marcha: no se pudo crear {self.path}") from exc
        with os.fdopen(fd, "w") as fh:
            fh.write(str(os.getpid()))
        self._acquired = True

    def release(self) -> None:
        if self._acquired and self.path.exists():
            self.path.unlink()
        self._acquired = False

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Vigila Wallapop/Vinted y notifica por Telegram cuando aparece una ganga."
    )
    parser.add_argument("--config", default="config.yaml", help="Ruta a config.yaml")
    parser.add_argument("--env-file", default=".env", help="Ruta al fichero .env con secretos")
    parser.add_argument("--db", default=None, help="Sobrescribe database_path de config.yaml")
    parser.add_argument(
        "--dry-run", action="store_true", help="No escribe en la BD ni envía nada a Telegram"
    )
    parser.add_argument(
        "--no-commands",
        action="store_true",
        help="No lee los comandos pendientes de Telegram (sí aplica los ajustes ya guardados)",
    )
    parser.add_argument("--verbose", action="store_true", help="Log en DEBUG")
    return parser


def configure_logging(level_name: str, verbose: bool) -> None:
    level = logging.DEBUG if verbose else getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


def run_search(
    search: SearchConfig,
    scraping_config: ScrapingConfig,
    storage: Storage,
    notifier: TelegramNotifier | None,
    dry_run: bool,
) -> dict[str, int]:
    """Ejecuta una búsqueda completa: scrape -> filtro -> dedup -> notifica.

    Deja que ScraperError se propague; quien llama aísla el fallo por búsqueda.
    """
    stats = {"found": 0, "passed_filter": 0, "new": 0, "sent": 0}

    with get_scraper(search.platform, scraping_config) as scraper:
        listings: list[Listing] = scraper.search(search.query, search)
    stats["found"] = len(listings)

    passed = apply_filters(listings, search)
    stats["passed_filter"] = len(passed)

    new_listings = storage.filter_new(passed)
    stats["new"] = len(new_listings)

    for listing in new_listings:
        if dry_run:
            logger.info(
                "[DRY-RUN] notificaría %s/%s: %s (%s) valoración=%s -> %s",
                listing.platform, listing.id, listing.title, listing.price_label(),
                listing.rating_label(), listing.url,
            )
            stats["sent"] += 1
            continue

        assert notifier is not None  # invariante: notifier solo es None en dry-run
        if notifier.send_listing(listing):
            storage.mark_notified(listing)
            stats["sent"] += 1
        else:
            logger.error(
                "No se pudo notificar %s/%s; no se marca como notificado, se reintentará en el próximo ciclo",
                listing.platform, listing.id,
            )

    return stats


def run_cycle(
    config: AppConfig,
    storage: Storage,
    dry_run: bool,
    notifier: TelegramNotifier | None = None,
) -> int:
    """Ejecuta todas las búsquedas configuradas. Devuelve el nº de búsquedas que fallaron."""
    if notifier is None and not dry_run:
        notifier = TelegramNotifier(config.telegram.bot_token, config.telegram.chat_id)

    totals = {"found": 0, "passed_filter": 0, "new": 0, "sent": 0}
    failures = 0

    for search in config.searches:
        label = f"{search.platform}:{search.query!r}"
        try:
            stats = run_search(search, config.scraping, storage, notifier, dry_run)
        except ScraperError as exc:
            failures += 1
            logger.error("Búsqueda %s falló, se continúa con el resto: %s", label, exc)
            continue
        except Exception:
            failures += 1
            logger.exception("Búsqueda %s falló con un error inesperado, se continúa con el resto", label)
            continue

        for key in totals:
            totals[key] += stats[key]
        logger.info(
            "Búsqueda %s: %d encontrados, %d pasan filtro, %d nuevos, %d enviados",
            label, stats["found"], stats["passed_filter"], stats["new"], stats["sent"],
        )

    logger.info(
        "Resumen del ciclo: %d búsquedas, %d encontrados, %d pasan filtro, %d nuevos, %d enviados, %d fallos",
        len(config.searches), totals["found"], totals["passed_filter"], totals["new"], totals["sent"], failures,
    )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config, args.env_file)
    except ConfigError as exc:
        logging.basicConfig(level=logging.ERROR, format="%(levelname)s: %(message)s")
        logging.getLogger(__name__).error("Configuración inválida: %s", exc)
        return 1

    configure_logging(config.log_level, args.verbose)

    db_path = Path(args.db) if args.db else Path(config.database_path)
    lock_path = db_path.parent / f"{db_path.stem}.lock"

    try:
        with FileLock(lock_path):
            with Storage(db_path, read_only=args.dry_run) as storage:
                notifier = (
                    None
                    if args.dry_run
                    else TelegramNotifier(config.telegram.bot_token, config.telegram.chat_id)
                )

                # Antes del ciclo, para que un /producto o un /precio recién
                # enviado ya afecte a esta misma pasada. En dry-run no se lee
                # nada: la BD está en solo-lectura y no se puede guardar el
                # offset, así que los comandos se reprocesarían indefinidamente.
                if notifier is not None and not args.no_commands:
                    try:
                        commands.process_updates(notifier, config, storage)
                    except Exception:
                        logger.exception(
                            "Fallo leyendo los comandos de Telegram; se sigue con los ajustes guardados"
                        )

                config = commands.apply_overrides(config, commands.load_overrides(storage))
                for search in config.searches:
                    logger.info(
                        "Búsqueda activa: %s %r, precio %.2f-%.2f, valoración >= %s",
                        search.platform, search.query, search.min_price,
                        search.price_threshold, search.seller_rating_threshold,
                    )

                failures = run_cycle(config, storage, args.dry_run, notifier)
    except LockError as exc:
        # No hay código de salida propio para "ya hay un ciclo en marcha" en el
        # plan; se trata como fallo del ciclo (2), ya que no se ejecutó ninguna búsqueda.
        logger.error("%s", exc)
        return 2

    if failures == len(config.searches) and failures > 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
