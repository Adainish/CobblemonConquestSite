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


def test_serialize_staff_application_includes_status_and_optional_fields():
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
