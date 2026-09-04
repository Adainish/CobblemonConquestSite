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
    config.ROADMAP_CHANGE_CHANNEL_ID = 0
    config.ROADMAP_CHANGE_MESSAGE = ""
    monkeypatch.setitem(sys.modules, "config", config)

    spec = importlib.util.spec_from_file_location("moderation_bot_under_test", _BOT_PATH)
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


def test_appeals_vote_passes_optional_message(bot_module, monkeypatch):
    interaction = _make_interaction()
    patch = mock.AsyncMock(return_value=(200, {"id": 14, "status": "denied"}))

    monkeypatch.setattr(bot_module, "_has_permission", lambda _: True)
    monkeypatch.setattr(bot_module, "_patch", patch)

    asyncio.run(
        bot_module.appeals_vote.callback(
            interaction,
            14,
            "deny",
            "Please open a new appeal after gathering more evidence.",
        )
    )

    patch.assert_awaited_once_with(
        "api/appeals/14",
        {
            "status": "denied",
            "message": "Please open a new appeal after gathering more evidence.",
        },
    )


def test_applications_force_accept_passes_optional_message(bot_module, monkeypatch):
    interaction = _make_interaction()
    patch = mock.AsyncMock(return_value=(200, {"id": 7, "status": "accepted"}))

    monkeypatch.setattr(bot_module, "_has_permission", lambda _: True)
    monkeypatch.setattr(bot_module, "_patch", patch)

    asyncio.run(
        bot_module.applications_force_accept.callback(
            interaction,
            7,
            "Please watch the staff channels for onboarding steps.",
        )
    )

    patch.assert_awaited_once_with(
        "api/staff-applications/7",
        {
            "status": "accepted",
            "message": "Please watch the staff channels for onboarding steps.",
        },
    )
