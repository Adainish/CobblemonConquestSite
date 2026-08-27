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

    def test_capture_roadmap_screenshot_uses_inner_main_when_present(
        self, bot_module, monkeypatch
    ):
        class FakeTarget:
            def __init__(self):
                async def _screenshot(*, path):
                    with open(path, "wb") as handle:
                        handle.write(b"png")

                self.screenshot = mock.AsyncMock(side_effect=_screenshot)

        class FakeLocator:
            def __init__(self, count):
                self._count = count
                self.first = FakeTarget()

            async def count(self):
                return self._count

        class FakePage:
            def __init__(self):
                self.goto = mock.AsyncMock()
                self.locators = {
                    "main main": FakeLocator(1),
                    "main": FakeLocator(2),
                }

            def locator(self, selector):
                return self.locators[selector]

        class FakeBrowser:
            def __init__(self):
                self.page = FakePage()
                self.new_page = mock.AsyncMock(return_value=self.page)
                self.close = mock.AsyncMock()

        class FakePlaywrightContext:
            def __init__(self):
                self.browser = FakeBrowser()
                self.chromium = types.SimpleNamespace(
                    launch=mock.AsyncMock(return_value=self.browser)
                )

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        fake_async_api = types.ModuleType("playwright.async_api")
        fake_async_api.Error = Exception
        fake_context = FakePlaywrightContext()
        fake_async_api.async_playwright = lambda: fake_context
        monkeypatch.setitem(sys.modules, "playwright.async_api", fake_async_api)

        screenshot_path = asyncio.run(bot_module._capture_roadmap_screenshot())

        assert os.path.exists(screenshot_path)
        fake_context.browser.page.locators[
            "main main"
        ].first.screenshot.assert_awaited_once_with(path=screenshot_path)
        fake_context.browser.page.locators["main"].first.screenshot.assert_not_awaited()
        os.unlink(screenshot_path)

    def test_clear_roadmap_notification_channel_uses_purge(self, bot_module):
        channel = mock.MagicMock()
        channel.purge = mock.AsyncMock()

        asyncio.run(bot_module._clear_roadmap_notification_channel(channel))

        channel.purge.assert_awaited_once_with(limit=None)

    def test_send_roadmap_change_notification_clears_channel_before_posting(
        self, bot_module, monkeypatch
    ):
        channel = mock.MagicMock()
        channel.send = mock.AsyncMock()
        clear_channel = mock.AsyncMock()

        monkeypatch.setattr(
            bot_module,
            "_resolve_roadmap_notification_channel",
            mock.AsyncMock(return_value=channel),
        )
        monkeypatch.setattr(
            bot_module,
            "_capture_roadmap_screenshot",
            mock.AsyncMock(return_value=None),
        )
        monkeypatch.setattr(bot_module, "_clear_roadmap_notification_channel", clear_channel)

        asyncio.run(
            bot_module._send_roadmap_change_notification(
                mock.sentinel.client,
                "updated",
                {"id": 3, "title": "Warzones", "status": "completed"},
            )
        )

        clear_channel.assert_awaited_once_with(channel)
        channel.send.assert_awaited_once()
