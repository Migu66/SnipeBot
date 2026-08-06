"""Persistencia SQLite: deduplicación de anuncios ya notificados."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import TracebackType

from models import Listing

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notified_listings (
    id TEXT NOT NULL,
    platform TEXT NOT NULL,
    price REAL NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    notified_at TIMESTAMP NOT NULL,
    PRIMARY KEY (platform, id)
);
"""


class Storage:
    """Acceso a la base de datos de deduplicación. Úsese como context manager."""

    def __init__(self, db_path: str | Path, read_only: bool = False) -> None:
        self.db_path = Path(db_path)
        self.read_only = read_only

        if read_only and self.db_path.exists():
            # Conexión de solo lectura sobre una BD ya existente: no toca disco.
            # Se asume que el esquema ya se creó en una ejecución normal previa.
            uri = f"file:{self.db_path.as_posix()}?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True)
        elif read_only:
            # No hay BD todavía: nada que deduplicar, se trabaja en memoria.
            self._conn = sqlite3.connect(":memory:")
            self.init_schema()
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self.init_schema()

    def init_schema(self) -> None:
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def is_notified(self, platform: str, listing_id: str) -> bool:
        cursor = self._conn.execute(
            "SELECT 1 FROM notified_listings WHERE platform = ? AND id = ?",
            (platform, listing_id),
        )
        return cursor.fetchone() is not None

    def filter_new(self, listings: list[Listing]) -> list[Listing]:
        """Devuelve los anuncios que todavía no se han notificado, en una sola consulta."""
        if not listings:
            return []

        keys = [listing.key for listing in listings]
        placeholders = ",".join("(?,?)" for _ in keys)
        params = [value for platform, listing_id in keys for value in (platform, listing_id)]

        cursor = self._conn.execute(
            f"SELECT platform, id FROM notified_listings WHERE (platform, id) IN ({placeholders})",
            params,
        )
        already_notified = {tuple(row) for row in cursor.fetchall()}

        return [listing for listing in listings if listing.key not in already_notified]

    def mark_notified(self, listing: Listing) -> None:
        """Registra un anuncio como notificado. Llamar solo tras un envío correcto."""
        if self.read_only:
            raise RuntimeError("Storage en modo solo-lectura: no se puede marcar como notificado")
        self._conn.execute(
            """
            INSERT OR IGNORE INTO notified_listings (id, platform, price, title, url, notified_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                listing.id,
                listing.platform,
                listing.price,
                listing.title,
                listing.url,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    def purge_older_than(self, days: int) -> int:
        """Borra registros de notificados con más de `days` días de antigüedad. Devuelve el nº borrado."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cursor = self._conn.execute(
            "DELETE FROM notified_listings WHERE notified_at < ?",
            (cutoff,),
        )
        self._conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
