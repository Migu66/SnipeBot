from __future__ import annotations

from dataclasses import replace

import httpx
import pytest
import respx

from scrapers.base import ScraperError
from scrapers.vinted import BASE_URL, SEARCH_URL, USER_URL, VintedScraper
from tests.conftest import load_fixture


@pytest.fixture
def vinted_search_config(search_config):
    return replace(search_config, platform="vinted")


@respx.mock
def test_bootstraps_session_then_parses(scraping_config, vinted_search_config):
    respx.get(f"{BASE_URL}/").mock(return_value=httpx.Response(200, text="<html></html>"))
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("vinted_catalog.json"))
    )
    respx.get(USER_URL.format(user_id="55")).mock(
        return_value=httpx.Response(200, json=load_fixture("vinted_user.json"))
    )

    with VintedScraper(scraping_config) as scraper:
        listings = scraper.search("fujifilm x100v", vinted_search_config)

    # El segundo item, sin precio, se descarta.
    assert len(listings) == 1
    listing = listings[0]
    assert listing.id == "111"
    assert listing.title == "Fujifilm X100V vinted"
    assert listing.price == 820.0
    assert listing.currency == "EUR"
    assert listing.image_url == "https://images.vinted.net/111.jpg"
    assert listing.seller_rating == pytest.approx(4.8)
    assert listing.seller_review_count == 40
    assert listing.platform == "vinted"
    assert listing.condition == "Muy bueno"


@respx.mock
def test_rebootstraps_once_on_401(scraping_config, vinted_search_config):
    home_route = respx.get(f"{BASE_URL}/").mock(
        return_value=httpx.Response(200, text="<html></html>")
    )
    respx.get(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(401),
            httpx.Response(200, json=load_fixture("vinted_catalog.json")),
        ]
    )
    respx.get(USER_URL.format(user_id="55")).mock(return_value=httpx.Response(200, json={}))

    with VintedScraper(scraping_config) as scraper:
        listings = scraper.search("fujifilm x100v", vinted_search_config)

    assert len(listings) == 1
    assert len(home_route.calls) == 2  # bootstrap inicial + re-bootstrap tras el 401


@respx.mock
def test_gives_up_after_second_401(scraping_config, vinted_search_config):
    respx.get(f"{BASE_URL}/").mock(return_value=httpx.Response(200, text="<html></html>"))
    search_route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(401))

    with VintedScraper(scraping_config) as scraper:
        with pytest.raises(ScraperError):
            scraper.search("fujifilm x100v", vinted_search_config)

    assert len(search_route.calls) == 2  # intento original + un único re-bootstrap


@respx.mock
def test_unrecognized_shape_returns_empty(scraping_config, vinted_search_config):
    respx.get(f"{BASE_URL}/").mock(return_value=httpx.Response(200, text="<html></html>"))
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"unexpected": []}))

    with VintedScraper(scraping_config) as scraper:
        listings = scraper.search("fujifilm x100v", vinted_search_config)

    assert listings == []
