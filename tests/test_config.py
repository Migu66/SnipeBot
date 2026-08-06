from __future__ import annotations

import pytest

from config import ConfigError, load_config

VALID_YAML = """
searches:
  - platform: wallapop
    query: "fujifilm x100v"
    price_threshold: 900
    seller_rating_threshold: 4.5
    min_price: 200
    min_seller_reviews: 3

scraping:
  request_delay_seconds: 3.0

database_path: data/snipebot.db
log_level: INFO
"""


def _write(tmp_path, yaml_text, env_text="TELEGRAM_BOT_TOKEN=123:ABC\nTELEGRAM_CHAT_ID=999\n"):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml_text, encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text(env_text, encoding="utf-8")
    return config_path, env_path


def test_load_config_valid(tmp_path):
    config_path, env_path = _write(tmp_path, VALID_YAML)

    config = load_config(config_path, env_path)

    assert len(config.searches) == 1
    assert config.searches[0].platform == "wallapop"
    assert config.searches[0].price_threshold == 900
    assert config.telegram.bot_token == "123:ABC"
    assert config.telegram.chat_id == "999"
    assert config.scraping.request_delay_seconds == 3.0
    assert config.database_path == "data/snipebot.db"
    assert config.log_level == "INFO"


def test_missing_config_file(tmp_path):
    with pytest.raises(ConfigError, match="no se encuentra"):
        load_config(tmp_path / "nope.yaml", tmp_path / ".env")


def test_empty_searches_rejected(tmp_path):
    config_path, env_path = _write(tmp_path, "searches: []\n")
    with pytest.raises(ConfigError, match="searches"):
        load_config(config_path, env_path)


def test_invalid_platform_rejected(tmp_path):
    yaml_text = VALID_YAML.replace("platform: wallapop", "platform: ebay")
    config_path, env_path = _write(tmp_path, yaml_text)
    with pytest.raises(ConfigError, match="platform"):
        load_config(config_path, env_path)


def test_rating_out_of_range_rejected(tmp_path):
    yaml_text = VALID_YAML.replace("seller_rating_threshold: 4.5", "seller_rating_threshold: 9")
    config_path, env_path = _write(tmp_path, yaml_text)
    with pytest.raises(ConfigError, match="seller_rating_threshold"):
        load_config(config_path, env_path)


def test_min_price_above_threshold_rejected(tmp_path):
    yaml_text = VALID_YAML.replace("min_price: 200", "min_price: 5000")
    config_path, env_path = _write(tmp_path, yaml_text)
    with pytest.raises(ConfigError, match="min_price"):
        load_config(config_path, env_path)


def test_request_delay_below_minimum_rejected(tmp_path):
    yaml_text = VALID_YAML.replace("request_delay_seconds: 3.0", "request_delay_seconds: 0.5")
    config_path, env_path = _write(tmp_path, yaml_text)
    with pytest.raises(ConfigError, match="request_delay_seconds"):
        load_config(config_path, env_path)


def test_missing_telegram_token_rejected(tmp_path):
    config_path, env_path = _write(tmp_path, VALID_YAML, env_text="TELEGRAM_CHAT_ID=999\n")
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        load_config(config_path, env_path)


def test_missing_telegram_chat_id_rejected(tmp_path):
    config_path, env_path = _write(tmp_path, VALID_YAML, env_text="TELEGRAM_BOT_TOKEN=123:ABC\n")
    with pytest.raises(ConfigError, match="TELEGRAM_CHAT_ID"):
        load_config(config_path, env_path)


def test_invalid_min_condition_rejected(tmp_path):
    yaml_text = VALID_YAML.replace(
        "min_seller_reviews: 3", "min_seller_reviews: 3\n    min_condition: impecable"
    )
    config_path, env_path = _write(tmp_path, yaml_text)
    with pytest.raises(ConfigError, match="min_condition"):
        load_config(config_path, env_path)


def test_valid_min_condition_accepted(tmp_path):
    yaml_text = VALID_YAML.replace(
        "min_seller_reviews: 3", "min_seller_reviews: 3\n    min_condition: Muy bueno"
    )
    config_path, env_path = _write(tmp_path, yaml_text)
    config = load_config(config_path, env_path)
    assert config.searches[0].min_condition == "Muy bueno"


def test_invalid_yaml_rejected(tmp_path):
    config_path, env_path = _write(tmp_path, "searches: [this is not: valid yaml")
    with pytest.raises(ConfigError, match="YAML inválido"):
        load_config(config_path, env_path)
