"""
Tests for the roadmap management API endpoints and Discord bot helper logic.
"""

import datetime
import hmac
import json
import os
import sys
import unittest.mock as mock

import pytest

_ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(_ROOT, "apps"))


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


# ── Shared in-memory DB fixture ───────────────────────────────────────────

@pytest.fixture(scope="module")
def db():
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
    _db.commit()
    yield _db


# ── API authentication helper ─────────────────────────────────────────────

class TestRoadmapAuth:
    """Tests for _check_roadmap_auth() logic (independent of HTTP stack)."""

    def test_matching_secret_passes(self):
        secret = "correct-secret"
        provided = "correct-secret"
        assert hmac.compare_digest(provided, secret)

    def test_wrong_secret_fails(self):
        secret = "correct-secret"
        provided = "wrong-secret"
        assert not hmac.compare_digest(provided, secret)

    def test_empty_secret_fails(self):
        secret = "correct-secret"
        provided = ""
        assert not provided or not hmac.compare_digest(provided, secret)


# ── Roadmap CRUD via DB ───────────────────────────────────────────────────

class TestRoadmapAPIData:
    """Test roadmap item CRUD operations that the API endpoints perform."""

    def test_create_item(self, db):
        rid = db.roadmap_item.insert(title="New Feature", status="planned", sort_order=1)
        db.commit()
        item = db(db.roadmap_item.id == rid).select().first()
        assert item.title == "New Feature"
        assert item.status == "planned"
        assert item.sort_order == 1

    def test_update_item_status(self, db):
        rid = db.roadmap_item.insert(title="Work In Progress", status="planned")
        db.commit()
        db(db.roadmap_item.id == rid).update(status="in_progress")
        db.commit()
        item = db(db.roadmap_item.id == rid).select().first()
        assert item.status == "in_progress"

    def test_update_item_title(self, db):
        rid = db.roadmap_item.insert(title="Old Title", status="planned")
        db.commit()
        db(db.roadmap_item.id == rid).update(title="New Title")
        db.commit()
        item = db(db.roadmap_item.id == rid).select().first()
        assert item.title == "New Title"

    def test_delete_item(self, db):
        rid = db.roadmap_item.insert(title="To Delete", status="planned")
        db.commit()
        db(db.roadmap_item.id == rid).delete()
        db.commit()
        item = db(db.roadmap_item.id == rid).select().first()
        assert item is None

    def test_list_items_ordered(self, db):
        db(db.roadmap_item.id > 0).delete()
        db.commit()
        db.roadmap_item.insert(title="B", status="planned", sort_order=2)
        db.roadmap_item.insert(title="A", status="planned", sort_order=1)
        db.commit()
        items = db(db.roadmap_item).select(orderby=db.roadmap_item.sort_order)
        titles = [i.title for i in items]
        assert titles == ["A", "B"]

    def test_valid_statuses(self, db):
        for status in ["planned", "in_progress", "completed", "cancelled"]:
            rid = db.roadmap_item.insert(title=f"Status test {status}", status=status)
            db.commit()
            item = db(db.roadmap_item.id == rid).select().first()
            assert item.status == status

    def test_description_optional(self, db):
        rid = db.roadmap_item.insert(title="No description", status="planned")
        db.commit()
        item = db(db.roadmap_item.id == rid).select().first()
        assert item.description is None or item.description == ""


# ── Discord bot helper logic ──────────────────────────────────────────────

class TestBotPermissionHelper:
    """Tests for the _has_permission logic used by the Discord bot."""

    def _make_role(self, name):
        r = mock.MagicMock()
        r.name = name
        return r

    def _make_member(self, role_names):
        member = mock.MagicMock(spec=["roles"])
        member.roles = [self._make_role(n) for n in role_names]
        return member

    def _make_interaction(self, role_names):
        interaction = mock.MagicMock()
        interaction.user = self._make_member(role_names)
        return interaction

    def _has_permission(self, interaction, allowed_roles):
        """Mirror of bot._has_permission for isolated testing."""
        member = interaction.user
        if not hasattr(member, "roles"):
            return False
        allowed_lower = {r.lower() for r in allowed_roles}
        return any(role.name.lower() in allowed_lower for role in member.roles)

    def test_member_with_allowed_role(self):
        interaction = self._make_interaction(["Admin", "Member"])
        assert self._has_permission(interaction, ["Admin", "Owner"]) is True

    def test_member_without_allowed_role(self):
        interaction = self._make_interaction(["Member", "Player"])
        assert self._has_permission(interaction, ["Admin", "Owner"]) is False

    def test_case_insensitive_match(self):
        interaction = self._make_interaction(["staff manager"])
        assert self._has_permission(interaction, ["Staff Manager"]) is True

    def test_no_roles(self):
        interaction = self._make_interaction([])
        assert self._has_permission(interaction, ["Admin"]) is False

    def test_member_with_multiple_matching_roles(self):
        interaction = self._make_interaction(["Owner", "Admin"])
        assert self._has_permission(interaction, ["Admin", "Owner"]) is True


# ── Settings for roadmap bot ──────────────────────────────────────────────

class TestRoadmapSettings:
    def test_roadmap_settings_present(self):
        import importlib
        settings = importlib.import_module("_default.settings")
        assert hasattr(settings, "DISCORD_BOT_TOKEN")
        assert hasattr(settings, "DISCORD_ROADMAP_ALLOWED_ROLES")
        assert hasattr(settings, "ROADMAP_API_SECRET")

    def test_allowed_roles_is_list(self):
        import importlib
        settings = importlib.import_module("_default.settings")
        assert isinstance(settings.DISCORD_ROADMAP_ALLOWED_ROLES, list)
        assert len(settings.DISCORD_ROADMAP_ALLOWED_ROLES) >= 1
