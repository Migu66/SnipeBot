"""Registro de scrapers disponibles por nombre de plataforma."""

from __future__ import annotations

from config import ScrapingConfig
from scrapers.base import BaseScraper, RateLimitedError, ScraperError
from scrapers.vinted import VintedScraper
from scrapers.wallapop import WallapopScraper

SCRAPERS: dict[str, type[BaseScraper]] = {
    "wallapop": WallapopScraper,
    "vinted": VintedScraper,
}


def get_scraper(platform: str, scraping_config: ScrapingConfig) -> BaseScraper:
    try:
        scraper_cls = SCRAPERS[platform]
    except KeyError:
        raise ValueError(
            f"plataforma no soportada: {platform!r} (soportadas: {sorted(SCRAPERS)})"
        ) from None
    return scraper_cls(scraping_config)


__all__ = ["SCRAPERS", "get_scraper", "BaseScraper", "ScraperError", "RateLimitedError"]
