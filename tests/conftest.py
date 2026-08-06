from __future__ import annotations

import json
from pathlib import Path

import pytest

from config import ScrapingConfig, SearchConfig, TelegramConfig
from models import Listing
from storage import Storage

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _clean_telegram_env(monkeypatch):
    """Evita que el .env de un test contamine el os.environ de otro (load_dotenv no sobrescribe)."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)


@pytest.fixture
def scraping_config() -> ScrapingConfig:
    return ScrapingConfig(
        request_delay_seconds=0,
        timeout_seconds=5.0,
        max_retries=2,
        backoff_base_seconds=0,
        fetch_seller_details=True,
    )


@pytest.fixture
def search_config() -> SearchConfig:
    return SearchConfig(
        platform="wallapop",
        query="fujifilm x100v",
        price_threshold=900,
        seller_rating_threshold=4.5,
        min_price=200,
        min_seller_reviews=3,
        allow_unknown_rating=False,
        max_results=40,
    )


@pytest.fixture
def telegram_config() -> TelegramConfig:
    return TelegramConfig(bot_token="123456:TEST", chat_id="999")


@pytest.fixture
def storage(tmp_path) -> Storage:
    st = Storage(tmp_path / "snipebot.db")
    yield st
    st.close()


@pytest.fixture
def make_listing():
    def _make(**overrides) -> Listing:
        defaults = dict(
            id="1",
            title="Fujifilm X100V",
            price=800.0,
            currency="EUR",
            url="https://example.com/1",
            seller_rating=4.8,
            seller_review_count=10,
            image_url="https://example.com/1.jpg",
            platform="wallapop",
        )
        defaults.update(overrides)
        return Listing(**defaults)

    return _make
