"""
Database models for Cobblemon Conquest.
"""

import datetime

from pydal.validators import IS_EMAIL, IS_IN_SET, IS_NOT_EMPTY, IS_LENGTH

from py4web import Field

from .common import db

_now = lambda: datetime.datetime.now(datetime.timezone.utc)  # noqa: E731
_STAFF_ACCESS_LEVELS = ["viewer", "editor", "admin"]

# ── Roadmap ────────────────────────────────────────────────────────────────
db.define_table(
    "roadmap_item",
    Field("title", requires=IS_NOT_EMPTY()),
    Field("description", "text"),
    Field("status", default="planned",
          requires=IS_IN_SET(["planned", "in_progress", "completed", "cancelled"])),
    Field("sort_order", "integer", default=0),
    Field("created_on", "datetime", default=_now),
)

# ── Appeals ────────────────────────────────────────────────────────────────
db.define_table(
    "appeal",
    Field("appeal_type", requires=IS_IN_SET(["ban", "mute", "discord"])),
    Field("minecraft_username", requires=[IS_NOT_EMPTY(), IS_LENGTH(maxsize=64)]),
    Field("discord_username", requires=[IS_NOT_EMPTY(), IS_LENGTH(maxsize=100)]),
    Field("email", requires=[IS_NOT_EMPTY(), IS_EMAIL(), IS_LENGTH(maxsize=255)]),
    Field("reason", "text", requires=IS_NOT_EMPTY()),
    Field("punishment_reason", "text"),
    Field("why_unban", "text", requires=IS_NOT_EMPTY()),
    Field("status", default="open",
          requires=IS_IN_SET(["open", "approved", "denied", "closed"])),
    Field("discord_message_id", default=""),
    Field("submitted_on", "datetime", default=_now),
    Field("ip_hash", default=""),  # hashed submitter IP for spam-prevention
)

# ── Staff Applications ─────────────────────────────────────────────────────
db.define_table(
    "staff_application",
    Field("role", requires=IS_NOT_EMPTY()),
    Field("minecraft_username", requires=[IS_NOT_EMPTY(), IS_LENGTH(maxsize=64)]),
    Field("discord_username", requires=[IS_NOT_EMPTY(), IS_LENGTH(maxsize=100)]),
    Field("email", requires=[IS_NOT_EMPTY(), IS_EMAIL(), IS_LENGTH(maxsize=255)]),
    Field("age", "integer"),
    Field("timezone", requires=IS_NOT_EMPTY()),
    Field("hours_per_week", "integer"),
    Field("why_apply", "text", requires=IS_NOT_EMPTY()),
    Field("prior_experience", "text"),
    Field("anything_else", "text"),
    Field("status", default="pending",
          requires=IS_IN_SET(["pending", "accepted", "rejected", "on_hold"])),
    Field("discord_message_id", default=""),
    Field("submitted_on", "datetime", default=_now),
    Field("ip_hash", default=""),
)

# ── FAQ ────────────────────────────────────────────────────────────────────
db.define_table(
    "faq_item",
    Field("question", requires=IS_NOT_EMPTY()),
    Field("answer", "text", requires=IS_NOT_EMPTY()),
    Field("category", default="General"),
    Field("sort_order", "integer", default=0),
    Field("created_on", "datetime", default=_now),
)

# ── Staff portal ────────────────────────────────────────────────────────────
db.define_table(
    "staff_member",
    Field("discord_id", "string", unique=True, requires=IS_NOT_EMPTY()),
    Field("discord_username", requires=IS_NOT_EMPTY()),
    Field("access_level", default="viewer", requires=IS_IN_SET(_STAFF_ACCESS_LEVELS)),
    Field("discord_roles_json", "text", default="[]"),
    Field("last_seen_on", "datetime", default=_now),
    Field("created_on", "datetime", default=_now),
)

db.define_table(
    "staff_document",
    Field("title", requires=IS_NOT_EMPTY()),
    Field("slug", requires=[IS_NOT_EMPTY(), IS_LENGTH(maxsize=100)]),
    Field("markdown", "text", requires=IS_NOT_EMPTY()),
    Field("read_level", default="viewer", requires=IS_IN_SET(_STAFF_ACCESS_LEVELS)),
    Field("write_level", default="editor", requires=IS_IN_SET(_STAFF_ACCESS_LEVELS)),
    Field("created_by_discord_id", "string", requires=IS_NOT_EMPTY()),
    Field("updated_by_discord_id", "string", requires=IS_NOT_EMPTY()),
    Field("created_on", "datetime", default=_now),
    Field("updated_on", "datetime", default=_now, update=_now),
)

db.define_table(
    "staff_notification",
    Field("message", "text", requires=IS_NOT_EMPTY()),
    Field("created_by_discord_id", "string", requires=IS_NOT_EMPTY()),
    Field("created_on", "datetime", default=_now),
)

db.define_table(
    "staff_note",
    Field("target_discord_id", "string", requires=IS_NOT_EMPTY()),
    Field("message", "text", requires=IS_NOT_EMPTY()),
    Field("created_by_discord_id", "string", requires=IS_NOT_EMPTY()),
    Field("created_on", "datetime", default=_now),
)

db.commit()
