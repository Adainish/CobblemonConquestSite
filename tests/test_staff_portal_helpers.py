import os
import sys


_ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(_ROOT, "apps"))

from _default import controllers


def test_resolve_staff_access_level_prefers_highest_id_match(monkeypatch):
    monkeypatch.setattr(
        controllers.settings,
        "DISCORD_STAFF_ROLE_LEVELS",
        {"1": "viewer", "2": "admin"},
        raising=False,
    )
    monkeypatch.setattr(
        controllers.settings,
        "DISCORD_STAFF_ROLE_LEVELS_BY_NAME",
        {},
        raising=False,
    )

    assert controllers._resolve_staff_access_level(["1", "2"]) == "admin"


def test_resolve_staff_access_level_uses_role_name_fallback(monkeypatch):
    monkeypatch.setattr(
        controllers.settings,
        "DISCORD_STAFF_ROLE_LEVELS",
        {},
        raising=False,
    )
    monkeypatch.setattr(
        controllers.settings,
        "DISCORD_STAFF_ROLE_LEVELS_BY_NAME",
        {"helper": "viewer", "staff manager": "editor"},
        raising=False,
    )

    assert controllers._resolve_staff_access_level(["999"], ["Helper", "Staff Manager"]) == "editor"


def test_resolve_staff_access_level_returns_none_when_no_allowed_roles(monkeypatch):
    monkeypatch.setattr(controllers.settings, "DISCORD_STAFF_ROLE_LEVELS", {}, raising=False)
    monkeypatch.setattr(controllers.settings, "DISCORD_STAFF_ROLE_LEVELS_BY_NAME", {}, raising=False)
    assert controllers._resolve_staff_access_level(["7"], ["Member"]) is None


def test_can_manage_staff_note_requires_higher_access():
    assert controllers._can_manage_staff_note("admin", "editor") is True
    assert controllers._can_manage_staff_note("editor", "editor") is False
    assert controllers._can_manage_staff_note("viewer", "editor") is False
