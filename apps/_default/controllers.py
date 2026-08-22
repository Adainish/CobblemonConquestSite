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
import hmac
import json
import datetime as _dt
import logging
import socket
import struct

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


def _resolve_mc_host(host: str, port: int) -> tuple[str, int]:
    """Resolve a Minecraft SRV record for *host* and return (target_host, target_port).

    Minecraft clients look up ``_minecraft._tcp.<host>`` before connecting.
    If no SRV record exists the original host/port are returned unchanged.
    """
    try:
        import dns.resolver  # dnspython
        answers = dns.resolver.resolve(f"_minecraft._tcp.{host}", "SRV")
        record = min(answers, key=lambda r: (r.priority, r.weight))
        srv_host = str(record.target).rstrip(".")
        srv_port = int(record.port)
        logger.debug("MC SRV %s → %s:%s", host, srv_host, srv_port)
        return srv_host, srv_port
    except Exception:
        return host, port


def _mc_player_count(host: str, port: int = 25565, timeout: float = 3.0) -> dict:
    """Ping a Minecraft Java server and return {"online": int, "max": int, "reachable": bool}.

    Uses the 1.7+ Server List Ping protocol (a lightweight handshake + status request).
    Falls back to {"online": 0, "max": 0, "reachable": False} on any error.
    """
    try:
        host, port = _resolve_mc_host(host, port)
        with socket.create_connection((host, port), timeout=timeout) as sock:
            def _pack_varint(val: int) -> bytes:
                buf = b""
                while True:
                    part = val & 0x7F
                    val >>= 7
                    if val:
                        buf += bytes([part | 0x80])
                    else:
                        buf += bytes([part])
                        break
                return buf

            def _read_varint(s) -> int:
                num = 0
                shift = 0
                while True:
                    b = s.recv(1)
                    if not b:
                        raise OSError("socket closed")
                    byte = b[0]
                    num |= (byte & 0x7F) << shift
                    if not (byte & 0x80):
                        return num
                    shift += 7

            host_enc = host.encode("utf-8")
            # Handshake packet (id=0x00): protocol=-1 (unset), host, port, next=1 (status)
            handshake = (
                b"\x00"
                + _pack_varint(0)         # protocol version (any)
                + _pack_varint(len(host_enc))
                + host_enc
                + struct.pack(">H", port)
                + b"\x01"                 # next state: status
            )
            sock.sendall(_pack_varint(len(handshake)) + handshake)
            # Status request packet (id=0x00, empty payload)
            sock.sendall(b"\x01\x00")

            # Read the response length + packet
            _read_varint(sock)   # packet length (ignore)
            _read_varint(sock)   # packet id  (0x00)
            json_len = _read_varint(sock)

            data = b""
            while len(data) < json_len:
                chunk = sock.recv(json_len - len(data))
                if not chunk:
                    break
                data += chunk

            status = json.loads(data.decode("utf-8"))
            players = status.get("players", {})
            return {
                "online": players.get("online", 0),
                "max": players.get("max", 0),
                "reachable": True,
            }
    except Exception as exc:
        logger.debug("MC ping failed for %s:%s – %s", host, port, exc)
        return {"online": 0, "max": 0, "reachable": False}


# ── public pages ──────────────────────────────────────────────────────────

@action("api/player-count")
def api_player_count():
    """Return live player count as JSON. Called by the front-end every 60 s."""
    response.headers["Content-Type"] = "application/json"
    response.headers["Cache-Control"] = "no-store"

    host = settings.SERVER_IP
    port = getattr(settings, "SERVER_PORT", 25565)
    result = _mc_player_count(host, port)
    return json.dumps(result)


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
    if appeal_type not in ("ban", "mute", "discord"):
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
            suffix = {"ban": "banned", "mute": "muted"}.get(appeal_type, "punished")
            errors["why_unban"] = "Please explain why you should be un-" + suffix + "."

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
                colour = 0xE74C3C if appeal_type == "ban" else (0x7289DA if appeal_type == "discord" else 0xF39C12)
                unpunished = {"ban": "Unbanned", "mute": "Unmuted"}.get(appeal_type, "Un-punished")
                embed = {
                    "title": f"New {appeal_type.title()} Appeal #{appeal_id}",
                    "color": colour,
                    "fields": [
                        {"name": "Minecraft Username", "value": minecraft_username, "inline": True},
                        {"name": "Discord Username", "value": discord_username, "inline": True},
                        {"name": "Punishment Reason", "value": punishment_reason or "Not provided", "inline": False},
                        {"name": "Why They Should Be " + unpunished, "value": why_unban, "inline": False},
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


@action("<path:path>")
def redirect_404(path=None):
    return redirect(URL("index"))


# ── Roadmap management API ────────────────────────────────────────────────
# These endpoints are called by the Discord bot.  Each request must carry
# the secret key configured in settings.ROADMAP_API_SECRET as the value of
# the "X-Roadmap-Secret" HTTP header.

_VALID_STATUSES = {"planned", "in_progress", "completed", "cancelled"}


def _check_roadmap_auth():
    """Raise HTTP 403 if the request does not carry the correct API secret."""
    secret = getattr(settings, "ROADMAP_API_SECRET", "")
    if not secret:
        raise HTTP(500, "Roadmap API secret not configured on the server.")
    provided = request.headers.get("X-Roadmap-Secret", "")
    if not provided or not hmac.compare_digest(provided, secret):
        raise HTTP(403, "Forbidden")


@action("api/roadmap", method=["GET"])
def api_roadmap_list():
    """Return all roadmap items as JSON (public, no auth required)."""
    response.headers["Content-Type"] = "application/json"
    response.headers["Cache-Control"] = "no-store"
    items = db(db.roadmap_item).select(
        orderby=db.roadmap_item.sort_order | db.roadmap_item.created_on
    )
    return json.dumps(
        [
            {
                "id": row.id,
                "title": row.title,
                "description": row.description or "",
                "status": row.status,
                "sort_order": row.sort_order,
            }
            for row in items
        ]
    )


@action("api/roadmap", method=["POST"])
def api_roadmap_create():
    """Create a new roadmap item.  Requires X-Roadmap-Secret header."""
    _check_roadmap_auth()
    response.headers["Content-Type"] = "application/json"

    try:
        body = json.loads(request.body.read())
    except (ValueError, AttributeError):
        raise HTTP(400, "Invalid JSON body")

    title = (body.get("title") or "").strip()
    if not title:
        raise HTTP(400, "title is required")

    status = body.get("status", "planned").strip().lower()
    if status not in _VALID_STATUSES:
        raise HTTP(400, f"status must be one of: {', '.join(sorted(_VALID_STATUSES))}")

    description = (body.get("description") or "").strip()
    sort_order = body.get("sort_order", 0)
    try:
        sort_order = int(sort_order)
    except (TypeError, ValueError):
        sort_order = 0

    new_id = db.roadmap_item.insert(
        title=title,
        description=description,
        status=status,
        sort_order=sort_order,
    )
    db.commit()
    return json.dumps({"id": new_id, "title": title, "status": status})


@action("api/roadmap/<item_id:int>", method=["PATCH"])
def api_roadmap_update(item_id):
    """Update an existing roadmap item.  Requires X-Roadmap-Secret header."""
    _check_roadmap_auth()
    response.headers["Content-Type"] = "application/json"

    row = db(db.roadmap_item.id == item_id).select().first()
    if not row:
        raise HTTP(404, f"Roadmap item {item_id} not found")

    try:
        body = json.loads(request.body.read())
    except (ValueError, AttributeError):
        raise HTTP(400, "Invalid JSON body")

    updates = {}
    if "title" in body:
        title = (body["title"] or "").strip()
        if not title:
            raise HTTP(400, "title cannot be empty")
        updates["title"] = title
    if "description" in body:
        updates["description"] = (body["description"] or "").strip()
    if "status" in body:
        status = body["status"].strip().lower()
        if status not in _VALID_STATUSES:
            raise HTTP(400, f"status must be one of: {', '.join(sorted(_VALID_STATUSES))}")
        updates["status"] = status
    if "sort_order" in body:
        try:
            updates["sort_order"] = int(body["sort_order"])
        except (TypeError, ValueError):
            raise HTTP(400, "sort_order must be an integer")

    if not updates:
        raise HTTP(400, "No valid fields provided for update")

    db(db.roadmap_item.id == item_id).update(**updates)
    db.commit()
    row = db(db.roadmap_item.id == item_id).select().first()
    return json.dumps(
        {
            "id": row.id,
            "title": row.title,
            "description": row.description or "",
            "status": row.status,
            "sort_order": row.sort_order,
        }
    )


@action("api/roadmap/<item_id:int>", method=["DELETE"])
def api_roadmap_delete(item_id):
    """Delete a roadmap item.  Requires X-Roadmap-Secret header."""
    _check_roadmap_auth()
    response.headers["Content-Type"] = "application/json"

    row = db(db.roadmap_item.id == item_id).select().first()
    if not row:
        raise HTTP(404, f"Roadmap item {item_id} not found")

    db(db.roadmap_item.id == item_id).delete()
    db.commit()
    return json.dumps({"deleted": item_id})
