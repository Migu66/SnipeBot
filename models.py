"""Modelo de datos común a todas las plataformas."""

from __future__ import annotations

from dataclasses import dataclass

# Escala de estado de Vinted, de peor a mejor (valores reales observados en
# item["status"] de /api/v2/catalog/items, en español porque se consulta
# vinted.es). "Satisfactorio" no se ha observado en muestras pero es el
# quinto nivel conocido de la propia UI de Vinted.
CONDITION_RANK: dict[str, int] = {
    "satisfactorio": 1,
    "bueno": 2,
    "muy bueno": 3,
    "nuevo sin etiquetas": 4,
    "nuevo con etiquetas": 5,
}


@dataclass(frozen=True, slots=True)
class Listing:
    """Un anuncio normalizado, venga de donde venga.

    Todos los scrapers devuelven listas de este tipo, de forma que filters.py,
    storage.py y notifier.py no saben nada de Wallapop ni de Vinted.
    """

    id: str
    title: str
    price: float
    currency: str
    url: str
    seller_rating: float | None
    seller_review_count: int | None
    image_url: str | None
    platform: str  # "wallapop" | "vinted"
    condition: str | None = None  # texto tal cual lo devuelve la plataforma (p.ej. "Muy bueno")

    @property
    def key(self) -> tuple[str, str]:
        """Clave de deduplicación."""
        return (self.platform, self.id)

    def price_label(self) -> str:
        symbol = {"EUR": "€", "USD": "$", "GBP": "£"}.get(self.currency.upper(), self.currency)
        return f"{self.price:,.2f} {symbol}".replace(",", "@").replace(".", ",").replace("@", ".")

    def rating_label(self) -> str:
        if self.seller_rating is None:
            return "sin valoración conocida"
        label = f"{self.seller_rating:.1f}/5"
        if self.seller_review_count:
            label += f" ({self.seller_review_count} valoraciones)"
        return label
