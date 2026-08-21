"""
Controllers (actions) for Cobblemon Conquest.

URL layout (app is mounted at _default so paths are at site root):
  /                  → index (home)
  /modpack           → modpack info
  /voting            → vote links
  /roadmap           → development roadmap
  /appeals           → appeal type selection
  /appeals/ban       → ban appeal form / POST handler
  /appeals/mute      → mute appeal form / POST handler
  /apply             → staff application (role selection redirect)
  /apply/<role>      → staff application form / POST handler
"""

import hashlib
import json
import datetime as _dt
import logging

import requests

from py4web import action, redirect, URL, response, request, HTTP

from .common import db, session, T
from . import settings
from .models import *  # noqa: F401,F403 – ensure tables are defined

logger = logging.getLogger("conquest")


def _ctx(**extra) -> dict:
    """Return a base context dict with site-wide values for templates."""
    base = dict(
        store_url=settings.STORE_URL,
        discord_url=settings.DISCORD_INVITE_URL,
        server_ip=settings.SERVER_IP,
        modpack_url=settings.MODPACK_URL,
        modrinth_url=settings.MODRINTH_URL,
    )
    base.update(extra)
    return base


# ── helpers ───────────────────────────────────────────────────────────────

def _hash_ip(ip: str) -> str:
    """One-way hash of an IP address for spam detection without storing raw IPs."""
    return hashlib.sha256(ip.encode()).hexdigest()


def _send_discord_webhook(webhook_url: str, embed: dict) -> str | None:
    """Post an embed to a Discord webhook; return the message ID or None."""
    if not webhook_url:
        return None
    payload = {"embeds": [embed], "wait": True}
    try:
        resp = requests.post(
            webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        if resp.status_code in (200, 204):
            data = resp.json()
            return data.get("id")
    except Exception as exc:
        logger.warning("Discord webhook failed: %s", exc)
    return None


def _add_discord_reactions(webhook_url: str, message_id: str):
    """Add ✅ and ❌ vote reactions to a Discord message via the API.
    Requires a bot token rather than a plain webhook, so this is a no-op
    unless a bot token is configured in settings_private.py.
    """
    bot_token = getattr(settings, "DISCORD_BOT_TOKEN", None)
    channel_id = getattr(settings, "DISCORD_APPEALS_CHANNEL_ID", None)
    if not (bot_token and channel_id and message_id):
        return
    base = f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}/reactions"
    headers = {"Authorization": f"Bot {bot_token}"}
    for emoji in ["%E2%9C%85", "%E2%9D%8C"]:  # ✅ ❌ url-encoded
        try:
            requests.put(f"{base}/{emoji}/@me", headers=headers, timeout=5)
        except Exception as exc:
            logger.warning("Discord reaction failed: %s", exc)


# ── public pages ──────────────────────────────────────────────────────────

@action("index")
@action.uses("index.html", db, session, T)
def index():
    return _ctx()


@action("modpack")
@action.uses("modpack.html", db, T)
def modpack():
    return _ctx()


@action("voting")
@action.uses("voting.html", T)
def voting():
    return _ctx(vote_sites=settings.VOTE_SITES)


@action("roadmap")
@action.uses("roadmap.html", db, T)
def roadmap():
    items = db(db.roadmap_item).select(orderby=db.roadmap_item.sort_order | db.roadmap_item.created_on)
    return _ctx(items=items)


# ── appeals ───────────────────────────────────────────────────────────────

@action("appeals")
@action.uses("appeals.html", T)
def appeals():
    return _ctx()


@action("appeals/<appeal_type>", method=["GET", "POST"])
@action.uses("appeal_form.html", db, session, T)
def appeal_form(appeal_type="ban"):
    if appeal_type not in ("ban", "mute"):
        redirect(URL("appeals"))

    flash = ""
    errors = {}
    form_data = {}

    if request.method == "POST":
        form_data = {k: v.strip() for k, v in request.forms.items()}
        minecraft_username = form_data.get("minecraft_username", "")
        discord_username = form_data.get("discord_username", "")
        reason = form_data.get("reason", "")
        punishment_reason = form_data.get("punishment_reason", "")
        why_unban = form_data.get("why_unban", "")

        # Validation
        if not minecraft_username:
            errors["minecraft_username"] = "Minecraft username is required."
        if not discord_username:
            errors["discord_username"] = "Discord username is required."
        if not why_unban:
            errors["why_unban"] = "Please explain why you should be un-" + appeal_type + "ned."

        if not errors:
            ip_hash = _hash_ip(request.environ.get("REMOTE_ADDR", "unknown"))

            # Spam prevention: check for open appeal from same username
            open_appeal = db(
                (db.appeal.minecraft_username == minecraft_username)
                & (db.appeal.appeal_type == appeal_type)
                & (db.appeal.status == "open")
            ).select().first()

            if open_appeal:
                flash = (
                    "You already have an open "
                    + appeal_type
                    + " appeal under this username. "
                    "Please wait for it to be reviewed before submitting a new one."
                )
            else:
                appeal_id = db.appeal.insert(
                    appeal_type=appeal_type,
                    minecraft_username=minecraft_username,
                    discord_username=discord_username,
                    reason=reason,
                    punishment_reason=punishment_reason,
                    why_unban=why_unban,
                    ip_hash=ip_hash,
                )
                db.commit()

                # Notify Discord
                colour = 0xE74C3C if appeal_type == "ban" else 0xF39C12
                embed = {
                    "title": f"New {appeal_type.title()} Appeal #{appeal_id}",
                    "color": colour,
                    "fields": [
                        {"name": "Minecraft Username", "value": minecraft_username, "inline": True},
                        {"name": "Discord Username", "value": discord_username, "inline": True},
                        {"name": "Punishment Reason", "value": punishment_reason or "Not provided", "inline": False},
                        {"name": "Why They Should Be Un" + appeal_type + "ned", "value": why_unban, "inline": False},
                    ],
                    "footer": {"text": f"Appeal ID {appeal_id} • React ✅ to approve, ❌ to deny"},
                    "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                }
                msg_id = _send_discord_webhook(settings.DISCORD_APPEALS_WEBHOOK, embed)
                if msg_id:
                    db(db.appeal.id == appeal_id).update(discord_message_id=msg_id)
                    _add_discord_reactions(settings.DISCORD_APPEALS_WEBHOOK, msg_id)
                    db.commit()

                redirect(URL("appeal_submitted", vars={"type": appeal_type}))

    return _ctx(
        appeal_type=appeal_type,
        flash=flash,
        errors=errors,
        form_data=form_data,
    )


@action("appeal_submitted")
@action.uses("appeal_submitted.html", T)
def appeal_submitted():
    appeal_type = request.params.get("type", "ban")
    return _ctx(appeal_type=appeal_type)


# ── staff applications ────────────────────────────────────────────────────

@action("apply")
@action.uses("apply.html", T)
def apply():
    return _ctx(roles=settings.STAFF_ROLES)


@action("apply/<role>", method=["GET", "POST"])
@action.uses("apply_form.html", db, session, T)
def apply_form(role="helper"):
    # Normalise role to title-case and validate
    role_title = role.title()
    if role_title not in settings.STAFF_ROLES:
        redirect(URL("apply"))

    flash = ""
    errors = {}
    form_data = {}

    if request.method == "POST":
        form_data = {k: v.strip() for k, v in request.forms.items()}
        minecraft_username = form_data.get("minecraft_username", "")
        discord_username = form_data.get("discord_username", "")
        why_apply = form_data.get("why_apply", "")
        timezone = form_data.get("timezone", "")

        age_raw = form_data.get("age", "")
        hours_raw = form_data.get("hours_per_week", "")

        if not minecraft_username:
            errors["minecraft_username"] = "Minecraft username is required."
        if not discord_username:
            errors["discord_username"] = "Discord username is required."
        if not why_apply:
            errors["why_apply"] = "Please explain why you want to be a " + role_title + "."
        if not timezone:
            errors["timezone"] = "Timezone is required."

        age = None
        if age_raw:
            try:
                age = int(age_raw)
                if age < 13:
                    errors["age"] = "You must be at least 13 to apply."
            except ValueError:
                errors["age"] = "Age must be a number."

        hours = None
        if hours_raw:
            try:
                hours = int(hours_raw)
            except ValueError:
                errors["hours_per_week"] = "Hours must be a number."

        if not errors:
            ip_hash = _hash_ip(request.environ.get("REMOTE_ADDR", "unknown"))

            # Spam prevention: one pending application per username per role
            existing = db(
                (db.staff_application.minecraft_username == minecraft_username)
                & (db.staff_application.role == role_title)
                & (db.staff_application.status.belongs(["pending", "on_hold"]))
            ).select().first()

            if existing:
                flash = (
                    "You already have a pending application for "
                    + role_title
                    + ". Please wait for it to be reviewed."
                )
            else:
                app_id = db.staff_application.insert(
                    role=role_title,
                    minecraft_username=minecraft_username,
                    discord_username=discord_username,
                    age=age,
                    timezone=timezone,
                    hours_per_week=hours,
                    why_apply=why_apply,
                    prior_experience=form_data.get("prior_experience", ""),
                    anything_else=form_data.get("anything_else", ""),
                    ip_hash=ip_hash,
                )
                db.commit()

                embed = {
                    "title": f"New {role_title} Application #{app_id}",
                    "color": 0x2ECC71,
                    "fields": [
                        {"name": "Minecraft Username", "value": minecraft_username, "inline": True},
                        {"name": "Discord Username", "value": discord_username, "inline": True},
                        {"name": "Role", "value": role_title, "inline": True},
                        {"name": "Age", "value": str(age) if age else "Not provided", "inline": True},
                        {"name": "Timezone", "value": timezone, "inline": True},
                        {"name": "Hours/Week", "value": str(hours) if hours else "Not provided", "inline": True},
                        {"name": "Why Apply", "value": why_apply, "inline": False},
                        {"name": "Prior Experience", "value": form_data.get("prior_experience") or "None", "inline": False},
                    ],
                    "footer": {"text": f"Application ID {app_id}"},
                    "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                }
                msg_id = _send_discord_webhook(settings.DISCORD_STAFFAPPS_WEBHOOK, embed)
                if msg_id:
                    db(db.staff_application.id == app_id).update(discord_message_id=msg_id)
                    db.commit()

                redirect(URL("apply_submitted", vars={"role": role_title}))

    return _ctx(
        role=role_title,
        flash=flash,
        errors=errors,
        form_data=form_data,
    )


@action("apply_submitted")
@action.uses("apply_submitted.html", T)
def apply_submitted():
    role = request.params.get("role", "Helper")
    return _ctx(role=role)
