from __future__ import annotations

from pathlib import Path

import httpx
import respx

import main as main_module
from config import AppConfig, ScrapingConfig, SearchConfig, TelegramConfig
from storage import Storage

BOT_BASE = "https://api.telegram.org/bot123:ABC"


class FakeScraper:
    """Sustituye a un scraper real: no hace ninguna petición de red."""

    def __init__(self, listings):
        self._listings = listings

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def search(self, query, search_config):
        return self._listings


def _config(tmp_path, search) -> AppConfig:
    return AppConfig(
        searches=[search],
        telegram=TelegramConfig(bot_token="123:ABC", chat_id="999"),
        scraping=ScrapingConfig(request_delay_seconds=0, backoff_base_seconds=0),
        database_path=str(tmp_path / "snipebot.db"),
        log_level="INFO",
    )


def test_cycle_notifies_once_then_nothing_on_second_pass(tmp_path, monkeypatch, make_listing):
    listing = make_listing(price=800, seller_rating=4.8, seller_review_count=10)
    search = SearchConfig(
        platform="wallapop",
        query="fujifilm x100v",
        price_threshold=900,
        seller_rating_threshold=4.5,
        min_price=200,
        min_seller_reviews=3,
    )
    config = _config(tmp_path, search)

    monkeypatch.setattr(main_module, "get_scraper", lambda platform, scraping_config: FakeScraper([listing]))
    sent: list = []
    monkeypatch.setattr(
        "notifier.TelegramNotifier.send_listing",
        lambda self, listing: sent.append(listing) or True,
    )

    db_path = Path(config.database_path)

    with Storage(db_path) as storage:
        failures = main_module.run_cycle(config, storage, dry_run=False)
    assert failures == 0
    assert len(sent) == 1

    with Storage(db_path) as storage:
        failures = main_module.run_cycle(config, storage, dry_run=False)
    assert failures == 0
    assert len(sent) == 1  # segunda pasada: nada nuevo que notificar


def test_failed_notification_is_not_marked_and_retried_next_cycle(tmp_path, monkeypatch, make_listing):
    listing = make_listing(price=800, seller_rating=4.8, seller_review_count=10)
    search = SearchConfig(
        platform="wallapop",
        query="fujifilm x100v",
        price_threshold=900,
        seller_rating_threshold=4.5,
    )
    config = _config(tmp_path, search)

    monkeypatch.setattr(main_module, "get_scraper", lambda platform, scraping_config: FakeScraper([listing]))
    monkeypatch.setattr("notifier.TelegramNotifier.send_listing", lambda self, listing: False)

    db_path = Path(config.database_path)
    with Storage(db_path) as storage:
        main_module.run_cycle(config, storage, dry_run=False)
        assert storage.is_notified(listing.platform, listing.id) is False


def test_one_search_failing_does_not_stop_the_others(tmp_path, monkeypatch, make_listing):
    ok_listing = make_listing(id="ok", price=800, seller_rating=4.8, seller_review_count=10)
    failing_search = SearchConfig(
        platform="wallapop", query="broken", price_threshold=900, seller_rating_threshold=4.5
    )
    ok_search = SearchConfig(
        platform="vinted", query="fujifilm x100v", price_threshold=900, seller_rating_threshold=4.5
    )
    config = AppConfig(
        searches=[failing_search, ok_search],
        telegram=TelegramConfig(bot_token="123:ABC", chat_id="999"),
        scraping=ScrapingConfig(request_delay_seconds=0, backoff_base_seconds=0),
        database_path=str(tmp_path / "snipebot.db"),
        log_level="INFO",
    )

    from scrapers.base import ScraperError

    class BrokenScraper(FakeScraper):
        def search(self, query, search_config):
            raise ScraperError("endpoint cambiado")

    def fake_get_scraper(platform, scraping_config):
        if platform == "wallapop":
            return BrokenScraper([])
        return FakeScraper([ok_listing])

    monkeypatch.setattr(main_module, "get_scraper", fake_get_scraper)
    sent: list = []
    monkeypatch.setattr(
        "notifier.TelegramNotifier.send_listing",
        lambda self, listing: sent.append(listing) or True,
    )

    with Storage(Path(config.database_path)) as storage:
        failures = main_module.run_cycle(config, storage, dry_run=False)

    assert failures == 1
    assert len(sent) == 1
    assert sent[0].id == "ok"


def _write_cli_files(tmp_path, db_path, query="ps5", price_threshold=350):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        f"""
searches:
  - platform: vinted
    query: "{query}"
    price_threshold: {price_threshold}
    seller_rating_threshold: 4.5

database_path: {db_path.as_posix()}
""",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=123:ABC\nTELEGRAM_CHAT_ID=999\n", encoding="utf-8")
    return config_yaml, env_file


@respx.mock
def test_telegram_command_changes_the_search_in_the_same_cycle(tmp_path, monkeypatch, make_listing):
    """Un /producto enviado antes del ciclo ya cambia lo que se busca en ese ciclo."""
    db_path = tmp_path / "data" / "snipebot.db"
    config_yaml, env_file = _write_cli_files(tmp_path, db_path)

    respx.post(f"{BOT_BASE}/getUpdates").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {
                        "update_id": 42,
                        "message": {
                            "message_id": 1,
                            "chat": {"id": 999},
                            "text": "/producto xbox series x",
                        },
                    }
                ],
            },
        )
    )
    respx.post(f"{BOT_BASE}/sendMessage").mock(return_value=httpx.Response(200, json={"ok": True}))
    respx.post(f"{BOT_BASE}/sendPhoto").mock(return_value=httpx.Response(200, json={"ok": True}))

    queries: list[str] = []

    class RecordingScraper(FakeScraper):
        def search(self, query, search_config):
            queries.append(query)
            return self._listings

    listing = make_listing(platform="vinted", price=300, seller_rating=4.8, seller_review_count=10)
    monkeypatch.setattr(
        main_module, "get_scraper", lambda platform, scraping_config: RecordingScraper([listing])
    )

    exit_code = main_module.main(["--config", str(config_yaml), "--env-file", str(env_file)])

    assert exit_code == 0
    assert queries == ["xbox series x"]

    # Y sigue vigente en el siguiente ciclo, sin volver a mandar el comando.
    respx.post(f"{BOT_BASE}/getUpdates").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": []})
    )
    main_module.main(["--config", str(config_yaml), "--env-file", str(env_file)])
    assert queries == ["xbox series x", "xbox series x"]


@respx.mock
def test_no_commands_flag_skips_telegram_polling(tmp_path, monkeypatch, make_listing):
    db_path = tmp_path / "data" / "snipebot.db"
    config_yaml, env_file = _write_cli_files(tmp_path, db_path)

    updates = respx.post(f"{BOT_BASE}/getUpdates").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": []})
    )
    monkeypatch.setattr(main_module, "get_scraper", lambda platform, scraping_config: FakeScraper([]))

    exit_code = main_module.main(
        ["--config", str(config_yaml), "--env-file", str(env_file), "--no-commands"]
    )

    assert exit_code == 0
    assert not updates.called


def test_dry_run_cli_does_not_touch_database(tmp_path, monkeypatch, make_listing):
    db_path = tmp_path / "data" / "snipebot.db"
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        f"""
searches:
  - platform: wallapop
    query: "fujifilm x100v"
    price_threshold: 900
    seller_rating_threshold: 4.5

database_path: {db_path.as_posix()}
""",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=123:ABC\nTELEGRAM_CHAT_ID=999\n", encoding="utf-8")

    listing = make_listing(price=800, seller_rating=4.8, seller_review_count=10)
    monkeypatch.setattr(main_module, "get_scraper", lambda platform, scraping_config: FakeScraper([listing]))

    exit_code = main_module.main(
        ["--config", str(config_yaml), "--env-file", str(env_file), "--dry-run"]
    )

    assert exit_code == 0
    assert not db_path.exists()
    assert not (db_path.parent / "snipebot.lock").exists()
