import asyncio
import importlib.util
import os
import sys
import types
import unittest.mock as mock

import pytest

_ROOT = os.path.dirname(os.path.dirname(__file__))
_BOT_PATH = os.path.join(_ROOT, "discord_bot", "bot.py")


@pytest.fixture
def bot_module(monkeypatch):
    config = types.ModuleType("config")
    config.BOT_TOKEN = "token"
    config.SITE_BASE_URL = "https://example.com"
    config.ROADMAP_API_SECRET = "secret"
    config.ALLOWED_ROLES = ["Admin"]
    config.ROADMAP_CHANGE_CHANNEL_ID = 123456789
    config.ROADMAP_CHANGE_MESSAGE = (
        "Roadmap {action_label}: #{item_id} {title} {status_label} {roadmap_url}"
    )
    monkeypatch.setitem(sys.modules, "config", config)

    spec = importlib.util.spec_from_file_location("roadmap_bot_under_test", _BOT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _make_interaction():
    interaction = mock.MagicMock()
    interaction.client = mock.sentinel.client
    interaction.response.defer = mock.AsyncMock()
    interaction.followup.send = mock.AsyncMock()
    return interaction


class TestRoadmapChangeNotifications:
    def test_format_roadmap_change_message_uses_configured_template(self, bot_module):
        message = bot_module._format_roadmap_change_message(
            "updated",
            {
                "id": 9,
                "title": "Quest Board",
                "status": "in_progress",
                "description": "New daily quests",
            },
        )

        assert message == (
            "Roadmap updated: #9 Quest Board 🔨 In Progress https://example.com/roadmap"
        )

    def test_format_roadmap_change_message_falls_back_for_invalid_template(self, bot_module):
        bot_module.config.ROADMAP_CHANGE_MESSAGE = "Roadmap {"

        message = bot_module._format_roadmap_change_message(
            "added",
            {"id": 4, "title": "Guild Wars", "status": "planned"},
        )

        assert message == "🗺️ Roadmap added: **#4** Guild Wars\nhttps://example.com/roadmap"

    def test_roadmap_add_schedules_change_notification(self, bot_module, monkeypatch):
        interaction = _make_interaction()
        created_item = {
            "id": 5,
            "title": "New Region",
            "description": "Explore a new island",
            "status": "planned",
        }

        monkeypatch.setattr(bot_module, "_has_permission", lambda _: True)
        monkeypatch.setattr(bot_module, "_post", mock.AsyncMock(return_value=(200, created_item)))
        schedule_notification = mock.MagicMock()
        monkeypatch.setattr(
            bot_module, "_schedule_roadmap_change_notification", schedule_notification
        )

        asyncio.run(
            bot_module.roadmap_add.callback(
                interaction,
                "New Region",
                "Explore a new island",
                "planned",
                0,
            )
        )

        interaction.followup.send.assert_awaited_once()
        schedule_notification.assert_called_once_with(
            interaction.client, "added", created_item
        )

    def test_roadmap_edit_schedules_change_notification(self, bot_module, monkeypatch):
        interaction = _make_interaction()
        updated_item = {
            "id": 7,
            "title": "Faction PvP",
            "description": "Clan battle improvements",
            "status": "completed",
        }

        monkeypatch.setattr(bot_module, "_has_permission", lambda _: True)
        monkeypatch.setattr(
            bot_module, "_patch", mock.AsyncMock(return_value=(200, updated_item))
        )
        schedule_notification = mock.MagicMock()
        monkeypatch.setattr(
            bot_module, "_schedule_roadmap_change_notification", schedule_notification
        )

        asyncio.run(
            bot_module.roadmap_edit.callback(
                interaction,
                7,
                "Faction PvP",
                "Clan battle improvements",
                "completed",
                3,
            )
        )

        interaction.followup.send.assert_awaited_once()
        schedule_notification.assert_called_once_with(
            interaction.client, "updated", updated_item
        )

    def test_roadmap_remove_schedules_change_notification(self, bot_module, monkeypatch):
        interaction = _make_interaction()
        deleted_item = {
            "id": 8,
            "title": "Trading Hub",
            "description": "Player market area",
            "status": "planned",
        }

        monkeypatch.setattr(bot_module, "_has_permission", lambda _: True)
        monkeypatch.setattr(
            bot_module,
            "_roadmap_change_notifications_enabled",
            lambda: True,
        )
        monkeypatch.setattr(
            bot_module, "_get_roadmap_item", mock.AsyncMock(return_value=deleted_item)
        )
        monkeypatch.setattr(
            bot_module, "_delete", mock.AsyncMock(return_value=(200, {"deleted": 8}))
        )
        schedule_notification = mock.MagicMock()
        monkeypatch.setattr(
            bot_module, "_schedule_roadmap_change_notification", schedule_notification
        )

        asyncio.run(bot_module.roadmap_remove.callback(interaction, 8))

        interaction.followup.send.assert_awaited_once()
        schedule_notification.assert_called_once_with(
            interaction.client, "removed", deleted_item
        )
