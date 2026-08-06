"""Filtro de anuncios: precio, valoración del vendedor y nº de reseñas."""

from __future__ import annotations

from config import SearchConfig
from models import CONDITION_RANK, Listing


def evaluate(listing: Listing, search: SearchConfig) -> tuple[bool, str]:
    """Evalúa un anuncio contra los umbrales de una búsqueda.

    Devuelve (cumple, motivo). El motivo describe el descarte (o "ok") para
    poder depurar en modo DEBUG sin tener que adivinar por qué se filtró algo.
    """
    if listing.price > search.price_threshold:
        return False, f"precio {listing.price} > umbral {search.price_threshold}"

    if listing.price < search.min_price:
        return False, f"precio {listing.price} < mínimo {search.min_price}"

    if listing.seller_rating is None:
        if not search.allow_unknown_rating:
            return False, "valoración del vendedor desconocida y allow_unknown_rating=false"
    else:
        if listing.seller_rating < search.seller_rating_threshold:
            return (
                False,
                f"valoración {listing.seller_rating} < umbral {search.seller_rating_threshold}",
            )
        review_count = listing.seller_review_count or 0
        if review_count < search.min_seller_reviews:
            return False, f"{review_count} reseñas < mínimo {search.min_seller_reviews}"

    if search.min_condition is not None:
        if listing.condition is None:
            return False, "condición desconocida y hay un mínimo de condición configurado"
        condition_rank = CONDITION_RANK.get(listing.condition.lower())
        if condition_rank is None:
            return False, f"condición '{listing.condition}' no reconocida"
        if condition_rank < CONDITION_RANK[search.min_condition.lower()]:
            return False, f"condición '{listing.condition}' < mínimo '{search.min_condition}'"

    return True, "ok"


def apply_filters(listings: list[Listing], search: SearchConfig) -> list[Listing]:
    return [listing for listing in listings if evaluate(listing, search)[0]]
