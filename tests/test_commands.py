from __future__ import annotations

import json

import httpx
import pytest
import respx

import commands
from config import AppConfig, ScrapingConfig, SearchConfig, TelegramConfig
from notifier import TelegramNotifier

BOT_BASE = "https://api.telegram.org/bot123:ABC"
CHAT_ID = "999"


@pytest.fixture
def notifier() -> TelegramNotifier:
    return TelegramNotifier(bot_token="123:ABC", chat_id=CHAT_ID, min_send_interval_seconds=0)


@pytest.fixture
def config() -> AppConfig:
    return AppConfig(
        searches=[
            SearchConfig(
                platform="vinted",
                query="ps5",
                price_threshold=350,
                seller_rating_threshold=4.5,
                min_price=250,
                min_seller_reviews=3,
                min_condition="muy bueno",
            )
        ],
        telegram=TelegramConfig(bot_token="123:ABC", chat_id=CHAT_ID),
        scraping=ScrapingConfig(request_delay_seconds=2, backoff_base_seconds=1),
        database_path="ignored.db",
    )


def _update(text: str, update_id: int = 1, chat_id: str = CHAT_ID) -> dict:
    return {
        "update_id": update_id,
        "message": {"message_id": update_id, "chat": {"id": int(chat_id)}, "text": text},
    }


def _mock_updates(*updates: dict):
    return respx.post(f"{BOT_BASE}/getUpdates").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": list(updates)})
    )


def _mock_replies():
    return respx.post(f"{BOT_BASE}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )


def _reply_texts(route) -> list[str]:
    return [json.loads(call.request.content)["text"] for call in route.calls]


# --- parseo -------------------------------------------------------------


@pytest.mark.parametrize(
    "text, name, args",
    [
        ("/precio 600", "precio", "600"),
        ("/PRECIO 600", "precio", "600"),
        ("/precio@SnipeBot 600", "precio", "600"),
        ("  /producto  xbox series x  ", "producto", "xbox series x"),
        ("/estado", "estado", ""),
    ],
)
def test_parse_command(text, name, args):
    command = commands.parse_command(text)
    assert command is not None
    assert (command.name, command.args) == (name, args)


def test_non_command_text_is_ignored():
    assert commands.parse_command("hola bot") is None


@pytest.mark.parametrize(
    "text, expected",
    [
        ("600", 600.0),
        ("600€", 600.0),
        ("600,50", 600.5),
        ("600.50", 600.5),
        ("1.200", 1200.0),
        ("1.200,50", 1200.5),
    ],
)
def test_parse_price_accepts_common_formats(text, expected):
    assert commands._parse_price(text) == expected


@pytest.mark.parametrize("text", ["", "barato", "600 euros y pico"])
def test_parse_price_rejects_junk(text):
    assert commands._parse_price(text) is None


# --- aplicación de ajustes ----------------------------------------------


def test_product_command_changes_the_query(config, storage):
    reply = commands.handle_command(
        commands.Command("producto", "xbox series x"), config, storage
    )

    assert "xbox series x" in reply
    updated = commands.apply_overrides(config, commands.load_overrides(storage))
    assert updated.searches[0].query == "xbox series x"
    # Lo que no se cambió sigue viniendo de config.yaml.
    assert updated.searches[0].price_threshold == 350
    assert updated.searches[0].min_condition == "muy bueno"


def test_price_command_changes_the_threshold(config, storage):
    commands.handle_command(commands.Command("precio", "600"), config, storage)

    updated = commands.apply_overrides(config, commands.load_overrides(storage))
    assert updated.searches[0].price_threshold == 600.0


def test_overrides_survive_a_new_storage_connection(config, tmp_path):
    from storage import Storage

    db_path = tmp_path / "persisted.db"
    with Storage(db_path) as first:
        commands.handle_command(commands.Command("producto", "xbox series x"), config, first)
        commands.handle_command(commands.Command("precio", "600"), config, first)

    with Storage(db_path) as second:
        updated = commands.apply_overrides(config, commands.load_overrides(second))

    assert updated.searches[0].query == "xbox series x"
    assert updated.searches[0].price_threshold == 600.0


def test_invalid_price_is_rejected_and_not_stored(config, storage):
    reply = commands.handle_command(commands.Command("precio", "-5"), config, storage)

    assert reply.startswith("❌")
    assert commands.load_overrides(storage) == {}


def test_price_below_min_price_is_rejected_with_a_useful_message(config, storage):
    # config tiene min_price=250: un máximo de 100 dejaría la búsqueda incoherente.
    reply = commands.handle_command(commands.Command("precio", "100"), config, storage)

    # El motivo se explica en castellano, sin nombres de campos ni entidades
    # HTML numéricas (Telegram solo documenta &lt;, &gt; y &amp;).
    assert "el precio mínimo" in reply
    assert "el precio máximo" in reply
    assert "min_price" not in reply
    assert "&#x27;" not in reply
    assert commands.load_overrides(storage) == {}


def test_min_price_can_be_lowered_to_allow_a_cheaper_max(config, storage):
    commands.handle_command(commands.Command("preciomin", "0"), config, storage)
    commands.handle_command(commands.Command("precio", "100"), config, storage)

    updated = commands.apply_overrides(config, commands.load_overrides(storage))
    assert (updated.searches[0].min_price, updated.searches[0].price_threshold) == (0.0, 100.0)


def test_missing_argument_replies_with_usage(config, storage):
    assert "Uso:" in commands.handle_command(commands.Command("producto", ""), config, storage)
    assert "Uso:" in commands.handle_command(commands.Command("precio", ""), config, storage)


def test_reset_restores_config_yaml(config, storage):
    commands.handle_command(commands.Command("producto", "xbox"), config, storage)
    commands.handle_command(commands.Command("reset", ""), config, storage)

    assert commands.load_overrides(storage) == {}
    assert commands.apply_overrides(config, commands.load_overrides(storage)).searches[0].query == "ps5"


def test_status_shows_the_effective_search(config, storage):
    commands.handle_command(commands.Command("producto", "xbox"), config, storage)

    reply = commands.handle_command(commands.Command("estado", ""), config, storage)

    assert "xbox" in reply
    assert "350,00 €" in reply


def test_unknown_command_replies_with_help(config, storage):
    reply = commands.handle_command(commands.Command("borra_todo", ""), config, storage)
    assert "/producto" in reply


def test_query_is_escaped_in_replies(config, storage):
    reply = commands.handle_command(
        commands.Command("producto", "ps5 <b>barata</b>"), config, storage
    )
    assert "&lt;b&gt;" in reply
    assert "<b>barata" not in reply


def test_corrupt_stored_override_is_ignored(config, storage):
    storage.set_setting(commands.OVERRIDE_PREFIX + "price_threshold", "no-es-un-numero")

    assert commands.load_overrides(storage) == {}


def test_invalid_override_falls_back_to_config(config):
    # Un ajuste que ya no encaja con config.yaml (p.ej. tras editar el fichero)
    # no debe dejar el ciclo sin búsquedas.
    updated = commands.apply_overrides(config, {"price_threshold": -1.0})

    assert updated.searches[0].price_threshold == 350


# --- lectura de updates -------------------------------------------------


@respx.mock
def test_process_updates_applies_command_and_replies(notifier, config, storage):
    _mock_updates(_update("/precio 600", update_id=7))
    replies = _mock_replies()

    handled = commands.process_updates(notifier, config, storage)

    assert handled == 1
    assert "600,00 €" in _reply_texts(replies)[0]
    assert commands.load_overrides(storage) == {"price_threshold": 600.0}


@respx.mock
def test_offset_is_stored_so_commands_are_not_reprocessed(notifier, config, storage):
    route = _mock_updates(_update("/precio 600", update_id=7))
    _mock_replies()

    commands.process_updates(notifier, config, storage)

    # La primera vez no hay nada que saltarse; después se pide a partir del
    # último update visto + 1, que es lo que hace que Telegram los descarte.
    assert "offset" not in json.loads(route.calls.last.request.content)
    assert storage.get_setting(commands.OFFSET_KEY) == "8"

    commands.process_updates(notifier, config, storage)
    assert json.loads(route.calls.last.request.content)["offset"] == 8


@respx.mock
def test_messages_from_other_chats_are_ignored(notifier, config, storage):
    _mock_updates(_update("/precio 600", update_id=3, chat_id="12345"))
    replies = _mock_replies()

    handled = commands.process_updates(notifier, config, storage)

    assert handled == 0
    assert not replies.called
    assert commands.load_overrides(storage) == {}
    # Pero sí se consume, para no volver a mirarlo en cada ciclo.
    assert storage.get_setting(commands.OFFSET_KEY) == "4"


@respx.mock
def test_edited_command_is_applied(notifier, config, storage):
    _mock_updates(
        {
            "update_id": 5,
            "edited_message": {"message_id": 1, "chat": {"id": 999}, "text": "/precio 600"},
        }
    )
    _mock_replies()

    assert commands.process_updates(notifier, config, storage) == 1
    assert commands.load_overrides(storage) == {"price_threshold": 600.0}


@respx.mock
def test_plain_text_is_not_answered(notifier, config, storage):
    _mock_updates(_update("hola", update_id=2))
    replies = _mock_replies()

    assert commands.process_updates(notifier, config, storage) == 0
    assert not replies.called


@respx.mock
def test_telegram_failure_does_not_raise(notifier, config, storage):
    respx.post(f"{BOT_BASE}/getUpdates").mock(return_value=httpx.Response(500))

    assert commands.process_updates(notifier, config, storage) == 0


@respx.mock
def test_several_commands_in_one_batch_are_all_applied(notifier, config, storage):
    _mock_updates(
        _update("/producto xbox series x", update_id=1),
        _update("/preciomin 0", update_id=2),
        _update("/precio 600", update_id=3),
    )
    _mock_replies()

    assert commands.process_updates(notifier, config, storage) == 3

    updated = commands.apply_overrides(config, commands.load_overrides(storage))
    assert updated.searches[0].query == "xbox series x"
    assert updated.searches[0].price_threshold == 600.0
    assert storage.get_setting(commands.OFFSET_KEY) == "4"
