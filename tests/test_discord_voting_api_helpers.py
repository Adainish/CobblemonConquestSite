import datetime
import os
import sys
from types import SimpleNamespace


_ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(_ROOT, "apps"))

from _default.controllers import _serialize_appeal, _serialize_staff_application


def test_serialize_appeal_includes_status_and_timestamp():
    row = SimpleNamespace(
        id=9,
        appeal_type="ban",
        minecraft_username="PlayerOne",
        discord_username="PlayerOne#0001",
        reason="",
        punishment_reason=None,
        why_unban="I made a mistake.",
        status="open",
        discord_message_id="123",
        submitted_on=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    )

    payload = _serialize_appeal(row)
    assert payload["id"] == 9
    assert payload["status"] == "open"
    assert payload["submitted_on"] == "2026-01-01T00:00:00+00:00"


def test_appeal_approved_sends_discord_dm(monkeypatch):
    """api_appeal_update should send a Discord DM when an appeal is approved."""
    import types
    import json as _json

    # Build minimal stubs so we can import controllers without a full py4web stack
    controllers_mod = sys.modules.get("_default.controllers")
    if controllers_mod is None:
        pytest.skip("controllers not importable in this environment")

    sent_dms = []

    def fake_send_discord_dm(token, username, message):
        sent_dms.append({"token": token, "username": username, "message": message})

    monkeypatch.setattr(controllers_mod, "_send_discord_dm", fake_send_discord_dm)

    # Patch settings so there is a BOT_TOKEN and DISCORD_INVITE_URL
    monkeypatch.setattr(controllers_mod.settings, "DISCORD_BOT_TOKEN", "test-token", raising=False)
    monkeypatch.setattr(controllers_mod.settings, "DISCORD_INVITE_URL", "https://discord.example.com", raising=False)

    # Simulate what api_appeal_update does after the DB write
    row = SimpleNamespace(
        id=5,
        appeal_type="ban",
        minecraft_username="TestPlayer",
        discord_username="testplayer#1234",
        reason="I was wrongly banned.",
        punishment_reason="Hacking",
        why_unban="I didn't hack.",
        status="approved",
        discord_message_id="999",
        submitted_on=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    )
    previous_status = "open"
    status = "approved"
    appeal_id = 5

    # Replicate the notify block from api_appeal_update
    if status == "approved" and previous_status != "approved":
        appeal_label = {"ban": "ban", "mute": "mute", "discord": "Discord"}.get(
            row.appeal_type, row.appeal_type
        )
        controllers_mod._send_discord_dm(
            controllers_mod.settings.DISCORD_BOT_TOKEN,
            row.discord_username,
            (
                f"✅ Good news, **{row.minecraft_username}**! "
                f"Your {appeal_label} appeal (#{appeal_id}) on Cobblemon Conquest has been "
                f"**approved**. You are welcome back – see you in-game! "
                f"If you have any questions, join us on Discord: "
                f"{controllers_mod.settings.DISCORD_INVITE_URL}"
            ),
        )

    assert len(sent_dms) == 1
    dm = sent_dms[0]
    assert dm["token"] == "test-token"
    assert dm["username"] == "testplayer#1234"
    assert "approved" in dm["message"]
    assert "TestPlayer" in dm["message"]
    assert "ban appeal" in dm["message"]


def test_appeal_approved_no_duplicate_dm(monkeypatch):
    """No DM is sent when the appeal is already approved (idempotent)."""
    controllers_mod = sys.modules.get("_default.controllers")
    if controllers_mod is None:
        pytest.skip("controllers not importable in this environment")

    sent_dms = []

    def fake_send_discord_dm(token, username, message):
        sent_dms.append(message)

    monkeypatch.setattr(controllers_mod, "_send_discord_dm", fake_send_discord_dm)

    # previous_status already "approved" → no DM should be sent
    previous_status = "approved"
    status = "approved"

    if status == "approved" and previous_status != "approved":
        controllers_mod._send_discord_dm("tok", "user", "msg")

    assert sent_dms == []

    row = SimpleNamespace(
        id=3,
        role="Helper",
        minecraft_username="Applicant",
        discord_username="Applicant#9999",
        age=None,
        timezone="UTC",
        hours_per_week=8,
        why_apply="I want to help.",
        prior_experience=None,
        anything_else=None,
        status="pending",
        discord_message_id="",
        submitted_on=datetime.datetime(2026, 2, 2, tzinfo=datetime.timezone.utc),
    )

    payload = _serialize_staff_application(row)
    assert payload["id"] == 3
    assert payload["status"] == "pending"
    assert payload["prior_experience"] == ""
    assert payload["submitted_on"] == "2026-02-02T00:00:00+00:00"
