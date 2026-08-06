from __future__ import annotations

import json

import httpx
import pytest
import respx

from notifier import TelegramNotifier

BOT_BASE = "https://api.telegram.org/bot123:ABC"


@pytest.fixture
def notifier() -> TelegramNotifier:
    return TelegramNotifier(bot_token="123:ABC", chat_id="999")


@respx.mock
def test_sends_photo_when_image_present(notifier, make_listing):
    route = respx.post(f"{BOT_BASE}/sendPhoto").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    listing = make_listing(title="<Fujifilm> & X100V", image_url="https://example.com/1.jpg")

    assert notifier.send_listing(listing) is True
    assert route.called
    body = route.calls.last.request.content.decode("utf-8")
    assert "&lt;Fujifilm&gt;" in body
    assert "<Fujifilm>" not in body


@respx.mock
def test_falls_back_to_send_message_if_photo_fails(notifier, make_listing):
    respx.post(f"{BOT_BASE}/sendPhoto").mock(
        return_value=httpx.Response(400, json={"ok": False, "description": "bad photo"})
    )
    message_route = respx.post(f"{BOT_BASE}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    listing = make_listing(image_url="https://example.com/broken.jpg")

    assert notifier.send_listing(listing) is True
    assert message_route.called


@respx.mock
def test_send_message_used_when_no_image(notifier, make_listing):
    route = respx.post(f"{BOT_BASE}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    listing = make_listing(image_url=None)

    assert notifier.send_listing(listing) is True
    assert route.called


@respx.mock
def test_failure_does_not_raise_and_returns_false(notifier, make_listing):
    respx.post(f"{BOT_BASE}/sendPhoto").mock(return_value=httpx.Response(500))
    respx.post(f"{BOT_BASE}/sendMessage").mock(return_value=httpx.Response(500))
    listing = make_listing()

    assert notifier.send_listing(listing) is False


@respx.mock
def test_telegram_ok_false_treated_as_failure(notifier, make_listing):
    respx.post(f"{BOT_BASE}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": False, "description": "chat not found"})
    )
    listing = make_listing(image_url=None)

    assert notifier.send_listing(listing) is False


@respx.mock
def test_caption_truncated_to_limit(notifier, make_listing):
    route = respx.post(f"{BOT_BASE}/sendPhoto").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    listing = make_listing(title="x" * 2000)

    notifier.send_listing(listing)

    payload = json.loads(route.calls.last.request.content)
    assert len(payload["caption"]) <= 1024
