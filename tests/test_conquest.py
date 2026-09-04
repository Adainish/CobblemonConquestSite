"""
Tests for Cobblemon Conquest – models and business logic.

Run with:  python -m pytest tests/ -v
"""

import datetime
import os
import sys

import pytest

# Ensure the apps folder is on the path before importing
_ROOT = os.path.dirname(os.path.dirname(__file__))
os.makedirs(os.path.join(_ROOT, "apps", "conquest", "databases"), exist_ok=True)
os.makedirs(os.path.join(_ROOT, "apps", "conquest", "uploads"), exist_ok=True)
os.makedirs(os.path.join(_ROOT, "apps", "conquest", "translations"), exist_ok=True)
sys.path.insert(0, os.path.join(_ROOT, "apps"))


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


@pytest.fixture(scope="module")
def db():
    """Fresh in-memory database for every test module run."""
    from py4web import DAL, Field

    _db = DAL("sqlite:memory", folder=None)

    _db.define_table(
        "roadmap_item",
        Field("title"),
        Field("description", "text"),
        Field("status", default="planned"),
        Field("sort_order", "integer", default=0),
        Field("created_on", "datetime", default=_now),
    )

    _db.define_table(
        "changelog_entry",
        Field("title"),
        Field("content", "text"),
        Field("created_on", "datetime", default=_now),
    )

    _db.define_table(
        "appeal",
        Field("appeal_type"),
        Field("minecraft_username"),
        Field("discord_username"),
        Field("reason", "text"),
        Field("punishment_reason", "text"),
        Field("why_unban", "text"),
        Field("status", default="open"),
        Field("discord_message_id", default=""),
        Field("submitted_on", "datetime", default=_now),
        Field("ip_hash", default=""),
    )

    _db.define_table(
        "staff_application",
        Field("role"),
        Field("minecraft_username"),
        Field("discord_username"),
        Field("age", "integer"),
        Field("timezone"),
        Field("hours_per_week", "integer"),
        Field("why_apply", "text"),
        Field("prior_experience", "text"),
        Field("anything_else", "text"),
        Field("status", default="pending"),
        Field("discord_message_id", default=""),
        Field("submitted_on", "datetime", default=_now),
        Field("ip_hash", default=""),
    )

    _db.commit()
    yield _db


# ── Roadmap tests ─────────────────────────────────────────────────────────

class TestRoadmap:
    def test_insert_planned(self, db):
        rid = db.roadmap_item.insert(title="Custom towns", status="planned")
        db.commit()
        item = db(db.roadmap_item.id == rid).select().first()
        assert item.title == "Custom towns"
        assert item.status == "planned"

    def test_insert_in_progress(self, db):
        rid = db.roadmap_item.insert(title="New biomes", status="in_progress")
        db.commit()
        item = db(db.roadmap_item.id == rid).select().first()
        assert item.status == "in_progress"

    def test_insert_completed(self, db):
        rid = db.roadmap_item.insert(title="Starter kits", status="completed")
        db.commit()
        item = db(db.roadmap_item.id == rid).select().first()
        assert item.status == "completed"

    def test_default_status_is_planned(self, db):
        rid = db.roadmap_item.insert(title="Unspecified feature")
        db.commit()
        item = db(db.roadmap_item.id == rid).select().first()
        assert item.status == "planned"


# ── Appeal tests ──────────────────────────────────────────────────────────

class TestChangelog:
    def test_insert_markdown_entry(self, db):
        cid = db.changelog_entry.insert(
            title="September Update",
            content="# Added\n- New battle tower",
        )
        db.commit()
        entry = db(db.changelog_entry.id == cid).select().first()
        assert entry.title == "September Update"
        assert entry.content.startswith("# Added")


class TestAppeals:
    def test_ban_appeal_insert(self, db):
        aid = db.appeal.insert(
            appeal_type="ban",
            minecraft_username="BanTestPlayer",
            discord_username="BanTestPlayer#1234",
            why_unban="I promise to follow the rules",
            ip_hash="hash1",
        )
        db.commit()
        appeal = db(db.appeal.id == aid).select().first()
        assert appeal.appeal_type == "ban"
        assert appeal.status == "open"

    def test_mute_appeal_insert(self, db):
        aid = db.appeal.insert(
            appeal_type="mute",
            minecraft_username="MuteTestPlayer",
            discord_username="MuteTestPlayer#5678",
            why_unban="I apologise for my language",
            ip_hash="hash2",
        )
        db.commit()
        appeal = db(db.appeal.id == aid).select().first()
        assert appeal.appeal_type == "mute"
        assert appeal.status == "open"

    def test_discord_appeal_insert(self, db):
        """Discord punishment appeals can be inserted and retrieved correctly."""
        aid = db.appeal.insert(
            appeal_type="discord",
            minecraft_username="DiscordTestPlayer",
            discord_username="DiscordTestPlayer#4321",
            why_unban="I was falsely banned from the Discord",
            ip_hash="hash_discord",
        )
        db.commit()
        appeal = db(db.appeal.id == aid).select().first()
        assert appeal.appeal_type == "discord"
        assert appeal.status == "open"

    def test_spam_prevention_open_appeal_detected(self, db):
        """Cannot submit a duplicate appeal while one is open."""
        db.appeal.insert(
            appeal_type="ban",
            minecraft_username="SpamPlayer",
            discord_username="SpamPlayer#0000",
            why_unban="First attempt",
            ip_hash="hash3",
        )
        db.commit()

        existing = db(
            (db.appeal.minecraft_username == "SpamPlayer")
            & (db.appeal.appeal_type == "ban")
            & (db.appeal.status == "open")
        ).select().first()

        assert existing is not None

    def test_no_spam_after_closed(self, db):
        """Once an appeal is closed the player can submit a new one."""
        aid = db.appeal.insert(
            appeal_type="ban",
            minecraft_username="ClosedPlayer",
            discord_username="ClosedPlayer#9999",
            why_unban="Old appeal",
            ip_hash="hash4",
        )
        db.commit()
        db(db.appeal.id == aid).update(status="denied")
        db.commit()

        existing = db(
            (db.appeal.minecraft_username == "ClosedPlayer")
            & (db.appeal.appeal_type == "ban")
            & (db.appeal.status == "open")
        ).select().first()

        assert existing is None

    def test_different_type_not_blocked(self, db):
        """A ban appeal should not block a mute appeal."""
        db.appeal.insert(
            appeal_type="ban",
            minecraft_username="MultiTypePlayer",
            discord_username="MultiTypePlayer#1111",
            why_unban="Ban appeal",
            ip_hash="hash5",
        )
        db.commit()

        mute_blocked = db(
            (db.appeal.minecraft_username == "MultiTypePlayer")
            & (db.appeal.appeal_type == "mute")
            & (db.appeal.status == "open")
        ).select().first()

        assert mute_blocked is None


# ── Staff application tests ────────────────────────────────────────────────

class TestStaffApplications:
    def test_insert_application(self, db):
        app_id = db.staff_application.insert(
            role="Helper",
            minecraft_username="HelperApplicant",
            discord_username="HelperApplicant#2222",
            timezone="UTC",
            why_apply="I want to help",
            ip_hash="hash6",
        )
        db.commit()
        app = db(db.staff_application.id == app_id).select().first()
        assert app.role == "Helper"
        assert app.status == "pending"

    def test_spam_prevention_pending_blocked(self, db):
        db.staff_application.insert(
            role="Helper",
            minecraft_username="DupeApplicant",
            discord_username="DupeApplicant#3333",
            timezone="UTC+10",
            why_apply="Excited to help!",
            ip_hash="hash7",
        )
        db.commit()

        existing = db(
            (db.staff_application.minecraft_username == "DupeApplicant")
            & (db.staff_application.role == "Helper")
            & (db.staff_application.status.belongs(["pending", "on_hold"]))
        ).select().first()

        assert existing is not None

    def test_different_role_not_blocked(self, db):
        """Applying for a different role should not be blocked."""
        db.staff_application.insert(
            role="Helper",
            minecraft_username="MultiRolePlayer",
            discord_username="MultiRolePlayer#0001",
            timezone="EST",
            why_apply="Helper application",
            ip_hash="hash8",
        )
        db.commit()

        moderator_blocked = db(
            (db.staff_application.minecraft_username == "MultiRolePlayer")
            & (db.staff_application.role == "Moderator")
            & (db.staff_application.status.belongs(["pending", "on_hold"]))
        ).select().first()

        assert moderator_blocked is None


# ── Settings tests ─────────────────────────────────────────────────────────

class TestSettings:
    def test_settings_importable(self):
        from conquest import settings
        assert settings.SERVER_IP
        assert settings.STORE_URL
        assert settings.DISCORD_INVITE_URL
        assert settings.MODPACK_URL
        assert len(settings.VOTE_SITES) >= 1
        assert "Helper" in settings.STAFF_ROLES

    def test_discord_webhooks_default_empty(self):
        from conquest import settings
        assert isinstance(settings.DISCORD_APPEALS_WEBHOOK, str)
        assert isinstance(settings.DISCORD_STAFFAPPS_WEBHOOK, str)


# ── Controller helper tests ────────────────────────────────────────────────

class TestHelpers:
    def test_hash_ip_deterministic(self):
        from conquest.controllers import _hash_ip
        assert _hash_ip("127.0.0.1") == _hash_ip("127.0.0.1")

    def test_hash_ip_different_values(self):
        from conquest.controllers import _hash_ip
        assert _hash_ip("127.0.0.1") != _hash_ip("192.168.0.1")

    def test_hash_ip_no_raw_ip(self):
        from conquest.controllers import _hash_ip
        result = _hash_ip("192.168.1.100")
        assert "192.168.1.100" not in result

    def test_ctx_contains_required_keys(self):
        from conquest.controllers import _ctx
        ctx = _ctx()
        assert "store_url" in ctx
        assert "discord_url" in ctx
        assert "server_ip" in ctx
        assert "modpack_url" in ctx

    def test_ctx_extra_kwargs_merged(self):
        from conquest.controllers import _ctx
        ctx = _ctx(items=[], role="Helper")
        assert ctx["items"] == []
        assert ctx["role"] == "Helper"
        assert "store_url" in ctx

    def test_sanitize_escapes_html(self):
        from conquest.controllers import _sanitize
        assert _sanitize("<script>alert('xss')</script>") == "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;"

    def test_sanitize_strips_whitespace(self):
        from conquest.controllers import _sanitize
        assert _sanitize("  hello  ") == "hello"

    def test_sanitize_ampersand(self):
        from conquest.controllers import _sanitize
        assert _sanitize("Tom & Jerry") == "Tom &amp; Jerry"

    def test_sanitize_plain_text_unchanged(self):
        from conquest.controllers import _sanitize
        assert _sanitize("Steve") == "Steve"
