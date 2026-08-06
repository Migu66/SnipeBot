from __future__ import annotations

from filters import apply_filters, evaluate


def test_within_thresholds_passes(search_config, make_listing):
    listing = make_listing(price=800, seller_rating=4.8, seller_review_count=10)
    ok, reason = evaluate(listing, search_config)
    assert ok
    assert reason == "ok"


def test_price_above_threshold_fails(search_config, make_listing):
    listing = make_listing(price=901)
    ok, reason = evaluate(listing, search_config)
    assert not ok
    assert "precio" in reason


def test_price_exactly_at_threshold_passes(search_config, make_listing):
    listing = make_listing(price=900, seller_rating=4.8, seller_review_count=10)
    ok, _ = evaluate(listing, search_config)
    assert ok


def test_price_below_minimum_fails(search_config, make_listing):
    listing = make_listing(price=199)
    ok, reason = evaluate(listing, search_config)
    assert not ok
    assert "mínimo" in reason


def test_price_exactly_at_minimum_passes(search_config, make_listing):
    listing = make_listing(price=200, seller_rating=4.8, seller_review_count=10)
    ok, _ = evaluate(listing, search_config)
    assert ok


def test_rating_below_threshold_fails(search_config, make_listing):
    listing = make_listing(seller_rating=4.4, seller_review_count=10)
    ok, reason = evaluate(listing, search_config)
    assert not ok
    assert "valoración" in reason


def test_rating_exactly_at_threshold_passes(search_config, make_listing):
    listing = make_listing(seller_rating=4.5, seller_review_count=10)
    ok, _ = evaluate(listing, search_config)
    assert ok


def test_unknown_rating_rejected_by_default(search_config, make_listing):
    listing = make_listing(seller_rating=None, seller_review_count=None)
    ok, reason = evaluate(listing, search_config)
    assert not ok
    assert "desconocida" in reason


def test_unknown_rating_allowed_when_configured(search_config, make_listing):
    from dataclasses import replace

    search = replace(search_config, allow_unknown_rating=True)
    listing = make_listing(seller_rating=None, seller_review_count=None)
    ok, _ = evaluate(listing, search)
    assert ok


def test_too_few_reviews_fails(search_config, make_listing):
    listing = make_listing(seller_rating=4.9, seller_review_count=1)
    ok, reason = evaluate(listing, search_config)
    assert not ok
    assert "reseñas" in reason


def test_condition_below_minimum_fails(search_config, make_listing):
    from dataclasses import replace

    search = replace(search_config, min_condition="muy bueno")
    listing = make_listing(condition="Bueno")
    ok, reason = evaluate(listing, search)
    assert not ok
    assert "condición" in reason


def test_condition_at_or_above_minimum_passes(search_config, make_listing):
    from dataclasses import replace

    search = replace(search_config, min_condition="muy bueno")
    listing = make_listing(condition="Nuevo con etiquetas")
    ok, _ = evaluate(listing, search)
    assert ok


def test_unknown_condition_rejected_when_min_condition_set(search_config, make_listing):
    from dataclasses import replace

    search = replace(search_config, min_condition="bueno")
    listing = make_listing(condition=None)
    ok, reason = evaluate(listing, search)
    assert not ok
    assert "desconocida" in reason


def test_condition_not_checked_when_min_condition_unset(search_config, make_listing):
    listing = make_listing(condition=None)
    ok, _ = evaluate(listing, search_config)
    assert ok


def test_apply_filters_returns_only_matching(search_config, make_listing):
    listings = [
        make_listing(id="ok", price=800, seller_rating=4.8, seller_review_count=10),
        make_listing(id="too-expensive", price=1000, seller_rating=4.8, seller_review_count=10),
        make_listing(id="bad-rating", price=800, seller_rating=3.0, seller_review_count=10),
    ]
    result = apply_filters(listings, search_config)
    assert [listing.id for listing in result] == ["ok"]
