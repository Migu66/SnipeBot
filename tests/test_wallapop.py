from __future__ import annotations

from dataclasses import replace

import httpx
import pytest
import respx

from scrapers.base import RateLimitedError
from scrapers.wallapop import SEARCH_URL, USER_URL, WallapopScraper
from tests.conftest import load_fixture


@respx.mock
def test_parses_old_search_objects_shape(scraping_config, search_config):
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("wallapop_search_old.json"))
    )
    respx.get(USER_URL.format(user_id="seller1")).mock(
        return_value=httpx.Response(200, json=load_fixture("wallapop_user.json"))
    )

    with WallapopScraper(scraping_config) as scraper:
        listings = scraper.search("fujifilm x100v", search_config)

    # El segundo item, sin precio, se descarta.
    assert len(listings) == 1
    listing = listings[0]
    assert listing.id == "abc123"
    assert listing.title == "Fujifilm X100V"
    assert listing.price == 850.0
    assert listing.currency == "EUR"
    assert listing.url == "https://es.wallapop.com/item/fujifilm-x100v-abc123"
    assert listing.image_url == "https://img.wallapop.com/abc123-big.jpg"
    assert listing.seller_rating == 4.8
    assert listing.seller_review_count == 25
    assert listing.platform == "wallapop"


@respx.mock
def test_parses_new_nested_payload_shape(scraping_config, search_config):
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("wallapop_search_new.json"))
    )
    respx.get(USER_URL.format(user_id="seller3")).mock(return_value=httpx.Response(200, json={}))

    with WallapopScraper(scraping_config) as scraper:
        listings = scraper.search("fujifilm x100v", search_config)

    assert len(listings) == 1
    listing = listings[0]
    assert listing.id == "xyz789"
    assert listing.price == 780.0
    assert listing.seller_rating is None
    assert listing.seller_review_count is None


@respx.mock
def test_unrecognized_shape_returns_empty(scraping_config, search_config):
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"unexpected": []}))

    with WallapopScraper(scraping_config) as scraper:
        listings = scraper.search("fujifilm x100v", search_config)

    assert listings == []


@respx.mock
def test_does_not_fetch_seller_when_disabled(scraping_config, search_config):
    scraping_config = replace(scraping_config, fetch_seller_details=False)
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("wallapop_search_old.json"))
    )
    user_route = respx.get(USER_URL.format(user_id="seller1")).mock(
        return_value=httpx.Response(200, json=load_fixture("wallapop_user.json"))
    )

    with WallapopScraper(scraping_config) as scraper:
        listings = scraper.search("fujifilm x100v", search_config)

    assert listings[0].seller_rating is None
    assert not user_route.called


@respx.mock
def test_retries_on_429_then_succeeds(scraping_config, search_config):
    route = respx.get(SEARCH_URL)
    route.mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json=load_fixture("wallapop_search_old.json")),
        ]
    )
    respx.get(USER_URL.format(user_id="seller1")).mock(return_value=httpx.Response(200, json={}))

    with WallapopScraper(scraping_config) as scraper:
        listings = scraper.search("fujifilm x100v", search_config)

    assert len(listings) == 1
    assert len(route.calls) == 2


@respx.mock
def test_gives_up_after_max_retries(scraping_config, search_config):
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(429))

    with WallapopScraper(scraping_config) as scraper:
        with pytest.raises(RateLimitedError):
            scraper.search("fujifilm x100v", search_config)
