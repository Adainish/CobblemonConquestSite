"""
Controllers (actions) for Cobblemon Conquest.

URL layout (app is mounted at _default so paths are at site root):
  /                  → index (home)
  /modpack           → modpack info
  /voting            → vote links
  /roadmap           → development roadmap
  /faq               → frequently asked questions
  /appeals           → appeal type selection
  /appeals/ban       → ban appeal form / POST handler
  /appeals/mute      → mute appeal form / POST handler
  /apply             → staff application (role selection redirect)
  /apply/<role>      → staff application form / POST handler
"""

import hashlib
import hmac
import html
import json
import datetime as _dt
import logging
import secrets
import smtplib
import socket
import struct
import urllib.parse
from email.message import EmailMessage

import requests

from py4web import action, redirect, URL, response, request, HTTP

from .common import db, session, T
from . import settings
from .models import *  # noqa: F401,F403 – ensure tables are defined

logger = logging.getLogger("conquest")
_STAFF_LEVEL_RANKS = {"viewer": 1, "editor": 2, "admin": 3}


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

def _sanitize(value: str) -> str:
    """Strip leading/trailing whitespace and HTML-escape a form field value."""
    return html.escape(value.strip())


def _hash_ip(ip: str) -> str:
    """One-way hash of an IP address for spam detection without storing raw IPs."""
    return hashlib.sha256(ip.encode()).hexdigest()


def _access_rank(level: str) -> int:
    return _STAFF_LEVEL_RANKS.get((level or "").strip().lower(), 0)


def _normalize_staff_level(level: str, default: str = "viewer") -> str:
    normalized = (level or "").strip().lower()
    if normalized in _STAFF_LEVEL_RANKS:
        return normalized
    return default


def _slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in (value or "").strip())
    slug = "-".join(part for part in cleaned.split("-") if part)
    return slug[:100] or "document"


def _resolve_staff_access_level(role_ids: list[str], role_names: list[str] | None = None) -> str | None:
    role_ids = [str(role_id) for role_id in (role_ids or [])]
    role_names = [str(name).strip().lower() for name in (role_names or []) if str(name).strip()]
    id_levels = getattr(settings, "DISCORD_STAFF_ROLE_LEVELS", {}) or {}
    name_levels_raw = getattr(settings, "DISCORD_STAFF_ROLE_LEVELS_BY_NAME", {}) or {}
    name_levels = {str(name).strip().lower(): value for name, value in name_levels_raw.items()}

    best = None
    best_rank = 0

    for role_id in role_ids:
        level = _normalize_staff_level(id_levels.get(role_id), default="")
        rank = _access_rank(level)
        if rank > best_rank:
            best = level
            best_rank = rank

    for role_name in role_names:
        level = _normalize_staff_level(name_levels.get(role_name), default="")
        rank = _access_rank(level)
        if rank > best_rank:
            best = level
            best_rank = rank

    return best


def _can_manage_staff_note(actor_level: str, target_level: str) -> bool:
    return _access_rank(actor_level) > _access_rank(target_level)


def _discord_role_name_lookup(guild_id: str) -> dict[str, str]:
    token = getattr(settings, "DISCORD_BOT_TOKEN", "")
    if not (token and guild_id):
        return {}
    headers = {"Authorization": f"Bot {token}"}
    try:
        resp = requests.get(
            f"https://discord.com/api/v10/guilds/{guild_id}/roles",
            headers=headers,
            timeout=8,
        )
        if not resp.ok:
            return {}
        data = resp.json()
        if not isinstance(data, list):
            return {}
        return {str(role.get("id")): str(role.get("name", "")) for role in data if role.get("id")}
    except (requests.RequestException, ValueError, TypeError):
        return {}


def _discord_username(user_data: dict) -> str:
    discriminator = user_data.get("discriminator")
    username = user_data.get("global_name") or user_data.get("username") or ""
    if discriminator and discriminator != "0" and user_data.get("username"):
        username = f"{user_data['username']}#{discriminator}"
    return username


def _get_discord_member(discord_id: str, guild_id: str) -> dict | None:
    token = getattr(settings, "DISCORD_BOT_TOKEN", "")
    if not token:
        raise HTTP(500, "DISCORD_BOT_TOKEN is required for staff role verification.")
    headers = {"Authorization": f"Bot {token}"}
    try:
        resp = requests.get(
            f"https://discord.com/api/v10/guilds/{guild_id}/members/{discord_id}",
            headers=headers,
            timeout=8,
        )
        if not resp.ok:
            return None
        return resp.json()
    except (requests.RequestException, ValueError, TypeError):
        return None


def _staff_identity_from_user(user_data: dict, member: dict, guild_id: str) -> dict | None:
    discord_id = str(user_data.get("id", "")).strip()
    if not discord_id:
        return None
    role_ids = [str(role_id) for role_id in member.get("roles", [])]
    role_name_lookup = _discord_role_name_lookup(guild_id)
    role_names = [role_name_lookup.get(role_id, "") for role_id in role_ids if role_id in role_name_lookup]
    access_level = _resolve_staff_access_level(role_ids, role_names)
    if not access_level:
        return None
    return {
        "discord_id": discord_id,
        "discord_username": _discord_username(user_data),
        "access_level": access_level,
        "discord_roles": role_ids,
    }


def _get_discord_staff_identity(access_token: str) -> dict | None:
    guild_id = str(getattr(settings, "DISCORD_STAFF_GUILD_ID", "")).strip()
    if not guild_id:
        raise HTTP(500, "DISCORD_STAFF_GUILD_ID is not configured")

    headers = {"Authorization": "Bearer " + access_token}
    try:
        user_resp = requests.get("https://discord.com/api/v10/users/@me", headers=headers, timeout=8)
        if not user_resp.ok:
            return None
        user = user_resp.json()
    except (requests.RequestException, ValueError, TypeError):
        return None

    discord_id = str(user.get("id", "")).strip()
    if not discord_id:
        return None
    member = _get_discord_member(discord_id, guild_id)
    if not member:
        return None
    return _staff_identity_from_user(user, member, guild_id)


def _clear_staff_session():
    session.pop("staff_auth", None)
    session.pop("staff_oauth_state", None)


def _sync_staff_member(auth: dict):
    existing = db(db.staff_member.discord_id == auth["discord_id"]).select().first()
    updates = dict(
        discord_username=auth["discord_username"],
        access_level=auth["access_level"],
        discord_roles_json=json.dumps(auth.get("discord_roles", [])),
        last_seen_on=_dt.datetime.now(_dt.timezone.utc),
    )
    if existing:
        db(db.staff_member.id == existing.id).update(**updates)
    else:
        db.staff_member.insert(discord_id=auth["discord_id"], **updates)
    db.commit()


def _require_staff(min_level: str = "viewer", redirect_on_missing: bool = False) -> dict:
    auth = session.get("staff_auth")
    if not auth or not auth.get("discord_id"):
        if redirect_on_missing:
            redirect(URL("staff_login"))
        raise HTTP(403, "Staff login required")

    guild_id = str(getattr(settings, "DISCORD_STAFF_GUILD_ID", "")).strip()
    if not guild_id:
        raise HTTP(500, "DISCORD_STAFF_GUILD_ID is not configured")
    member = _get_discord_member(auth["discord_id"], guild_id)
    if member:
        user_data = member.get("user") or {"id": auth["discord_id"], "username": auth.get("discord_username", "")}
    else:
        user_data = None
    identity = _staff_identity_from_user(user_data or {}, member or {}, guild_id) if user_data and member else None
    if not identity or not identity.get("discord_id"):
        _clear_staff_session()
        raise HTTP(403, "Your Discord role no longer grants staff access.")

    session["staff_auth"] = identity
    _sync_staff_member(identity)

    if _access_rank(identity["access_level"]) < _access_rank(min_level):
        raise HTTP(403, "Insufficient staff permissions")
    return identity


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


def _add_discord_reactions(webhook_url: str, message_id: str, emojis: list[str]):
    """Add reactions to a Discord message via webhook token."""
    if not (webhook_url and message_id and emojis):
        return

    base = webhook_url.rstrip("/")
    for emoji in emojis:
        try:
            encoded_emoji = requests.utils.quote(emoji, safe="")
            requests.put(
                f"{base}/messages/{message_id}/reactions/{encoded_emoji}/@me",
                timeout=5,
            )
        except Exception as exc:
            logger.warning("Discord reaction failed: %s", exc)


def _send_discord_dm(bot_token: str, discord_username: str, message: str):
    """Send a DM to a Discord user via the bot token.

    Looks up the user by username using the Discord REST API, opens (or
    retrieves) a DM channel, then sends *message* to that channel.
    Failures are logged as warnings so the web request is never blocked.
    """
    if not (bot_token and discord_username and message):
        return

    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }
    base = "https://discord.com/api/v10"

    try:
        # Search for the user by username so we can get their numeric ID.
        search_resp = requests.get(
            f"{base}/guilds",
            headers=headers,
            timeout=5,
        )
        # We can't search globally by username without a guild; instead use
        # the lookup endpoint available to bots: POST /users/@me/channels
        # requires knowing the recipient_id first.  We resolve the user ID
        # by searching across guilds the bot is in.
        recipient_id = None

        guilds_resp = requests.get(f"{base}/users/@me/guilds", headers=headers, timeout=5)
        if guilds_resp.ok:
            for guild in guilds_resp.json():
                members_resp = requests.get(
                    f"{base}/guilds/{guild['id']}/members/search",
                    headers=headers,
                    params={"query": discord_username, "limit": 5},
                    timeout=5,
                )
                if members_resp.ok:
                    for member in members_resp.json():
                        user = member.get("user", {})
                        # Match on username (case-insensitive) or global_name
                        if (
                            user.get("username", "").lower() == discord_username.lower()
                            or user.get("global_name", "").lower() == discord_username.lower()
                        ):
                            recipient_id = user["id"]
                            break
                if recipient_id:
                    break

        if not recipient_id:
            logger.warning(
                "Discord DM: could not resolve user ID for '%s'", discord_username
            )
            return

        # Open a DM channel with the user.
        dm_resp = requests.post(
            f"{base}/users/@me/channels",
            headers=headers,
            data=json.dumps({"recipient_id": recipient_id}),
            timeout=5,
        )
        if not dm_resp.ok:
            logger.warning(
                "Discord DM: failed to open DM channel for user %s: %s",
                recipient_id,
                dm_resp.text,
            )
            return

        channel_id = dm_resp.json()["id"]

        # Send the message.
        msg_resp = requests.post(
            f"{base}/channels/{channel_id}/messages",
            headers=headers,
            data=json.dumps({"content": message}),
            timeout=5,
        )
        if not msg_resp.ok:
            logger.warning(
                "Discord DM: failed to send message to channel %s: %s",
                channel_id,
                msg_resp.text,
            )

    except Exception as exc:
        logger.warning("Discord DM failed: %s", exc)


def _send_email(recipient: str, subject: str, body: str) -> bool:
    """Send a plain text email using SMTP settings; return True on success."""
    smtp_server = getattr(settings, "SMTP_SERVER", None)
    sender = getattr(settings, "SMTP_SENDER", "")
    if not (smtp_server and sender and recipient and subject and body):
        return False

    host = smtp_server
    port = 465 if getattr(settings, "SMTP_SSL", False) else 25
    if ":" in smtp_server:
        host, port_text = smtp_server.rsplit(":", 1)
        try:
            port = int(port_text)
        except ValueError:
            logger.warning("SMTP_SERVER has invalid port: %s", smtp_server)
            return False

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    login = getattr(settings, "SMTP_LOGIN", "") or ""
    username = ""
    password = ""
    if ":" in login:
        username, password = login.split(":", 1)
    elif login:
        username = login

    smtp_cls = smtplib.SMTP_SSL if getattr(settings, "SMTP_SSL", False) else smtplib.SMTP
    try:
        with smtp_cls(host, port, timeout=10) as server:
            if getattr(settings, "SMTP_TLS", False) and not getattr(settings, "SMTP_SSL", False):
                server.starttls()
            if username:
                server.login(username, password)
            server.send_message(message)
        return True
    except Exception as exc:
        logger.warning("Email send failed for %s: %s", recipient, exc)
        return False


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
    original_host, original_port = host, port
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
        logger.debug("Direct MC ping failed for %s:%s – %s", host, port, exc)

    # Fallback for production hosts where outbound game-port TCP is blocked.
    try:
        endpoint = f"https://api.mcstatus.io/v2/status/java/{original_host}"
        if int(original_port) != 25565:
            endpoint = f"{endpoint}:{int(original_port)}"

        api_resp = requests.get(endpoint, timeout=timeout)
        api_resp.raise_for_status()
        payload = api_resp.json()

        if not payload.get("online", False):
            return {"online": 0, "max": 0, "reachable": False}

        players = payload.get("players", {})
        return {
            "online": int(players.get("online", 0)),
            "max": int(players.get("max", 0)),
            "reachable": True,
        }
    except (requests.RequestException, ValueError, TypeError) as exc:
        logger.debug(
            "HTTP MC status fallback failed for %s:%s – %s",
            original_host,
            original_port,
            exc,
        )
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


@action("faq")
@action.uses("faq.html", db, T)
def faq():
    rows = db(db.faq_item).select(orderby=db.faq_item.sort_order | db.faq_item.created_on)
    # Group by category, preserving insertion order
    categories = {}
    for row in rows:
        cat = row.category or "General"
        categories.setdefault(cat, []).append(row)
    return _ctx(categories=categories)


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
        form_data = {k: _sanitize(v) for k, v in request.forms.items()}
        minecraft_username = form_data.get("minecraft_username", "")
        discord_username = form_data.get("discord_username", "")
        email = form_data.get("email", "")
        reason = form_data.get("reason", "")
        punishment_reason = form_data.get("punishment_reason", "")
        why_unban = form_data.get("why_unban", "")

        # Validation
        if not minecraft_username:
            errors["minecraft_username"] = "Minecraft username is required."
        if not discord_username:
            errors["discord_username"] = "Discord username is required."
        if not email:
            errors["email"] = "Email is required."
        elif "@" not in email:
            errors["email"] = "Please enter a valid email address."
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
                    email=email,
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
                    "footer": {"text": f"Appeal ID {appeal_id} • React ✅/❌/🔒 or use /appeals vote"},
                    "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                }
                msg_id = _send_discord_webhook(settings.DISCORD_APPEALS_WEBHOOK, embed)
                if msg_id:
                    db(db.appeal.id == appeal_id).update(discord_message_id=msg_id)
                    _add_discord_reactions(settings.DISCORD_APPEALS_WEBHOOK, msg_id, ["✅", "❌", "🔒"])
                    db.commit()

                _send_email(
                    email,
                    f"Cobblemon Conquest: {appeal_type.title()} appeal received (#{appeal_id})",
                    (
                        f"Hi {minecraft_username},\n\n"
                        f"We received your {appeal_type} appeal (ID #{appeal_id}). "
                        "Our staff team will review it and contact you once a decision is made.\n\n"
                        "Thanks,\nCobblemon Conquest Staff"
                    ),
                )

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
        form_data = {k: _sanitize(v) for k, v in request.forms.items()}
        minecraft_username = form_data.get("minecraft_username", "")
        discord_username = form_data.get("discord_username", "")
        email = form_data.get("email", "")
        why_apply = form_data.get("why_apply", "")
        timezone = form_data.get("timezone", "")

        age_raw = form_data.get("age", "")
        hours_raw = form_data.get("hours_per_week", "")

        if not minecraft_username:
            errors["minecraft_username"] = "Minecraft username is required."
        if not discord_username:
            errors["discord_username"] = "Discord username is required."
        if not email:
            errors["email"] = "Email is required."
        elif "@" not in email:
            errors["email"] = "Please enter a valid email address."
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
                    email=email,
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
                    "footer": {"text": f"Application ID {app_id} • React ✅/❌/⏸️ or use /applications vote"},
                    "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                }
                msg_id = _send_discord_webhook(settings.DISCORD_STAFFAPPS_WEBHOOK, embed)
                if msg_id:
                    db(db.staff_application.id == app_id).update(discord_message_id=msg_id)
                    _add_discord_reactions(settings.DISCORD_STAFFAPPS_WEBHOOK, msg_id, ["✅", "❌", "⏸️"])
                    db.commit()

                _send_email(
                    email,
                    f"Cobblemon Conquest: {role_title} application received (#{app_id})",
                    (
                        f"Hi {minecraft_username},\n\n"
                        f"We received your application for the {role_title} role (ID #{app_id}). "
                        "Our leadership team will review it and contact you once a decision is made.\n\n"
                        "Thanks,\nCobblemon Conquest Staff"
                    ),
                )

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


def _serialize_staff_document(row) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "slug": row.slug,
        "markdown": row.markdown or "",
        "read_level": row.read_level,
        "write_level": row.write_level,
        "created_by_discord_id": row.created_by_discord_id,
        "updated_by_discord_id": row.updated_by_discord_id,
        "updated_on": row.updated_on.isoformat() if row.updated_on else "",
    }


def _serialize_staff_notification(row) -> dict:
    return {
        "id": row.id,
        "message": row.message or "",
        "created_by_discord_id": row.created_by_discord_id,
        "created_on": row.created_on.isoformat() if row.created_on else "",
    }


def _serialize_staff_note(row) -> dict:
    return {
        "id": row.id,
        "target_discord_id": row.target_discord_id,
        "message": row.message or "",
        "created_by_discord_id": row.created_by_discord_id,
        "created_on": row.created_on.isoformat() if row.created_on else "",
    }


def _is_oauth_configured() -> bool:
    return bool(
        getattr(settings, "DISCORD_OAUTH_CLIENT_ID", "")
        and getattr(settings, "DISCORD_OAUTH_CLIENT_SECRET", "")
        and getattr(settings, "DISCORD_OAUTH_REDIRECT_URI", "")
        and getattr(settings, "DISCORD_STAFF_GUILD_ID", "")
    )


@action("staff/login")
@action.uses(session)
def staff_login():
    if not _is_oauth_configured():
        raise HTTP(500, "Discord OAuth staff login is not configured.")

    state = secrets.token_urlsafe(24)
    session["staff_oauth_state"] = state
    params = urllib.parse.urlencode(
        {
            "client_id": settings.DISCORD_OAUTH_CLIENT_ID,
            "redirect_uri": settings.DISCORD_OAUTH_REDIRECT_URI,
            "response_type": "code",
            "scope": "identify guilds.members.read",
            "state": state,
        }
    )
    redirect(f"https://discord.com/api/oauth2/authorize?{params}")


@action("staff/oauth/callback")
@action.uses(db, session)
def staff_oauth_callback():
    if not _is_oauth_configured():
        raise HTTP(500, "Discord OAuth staff login is not configured.")
    if request.query.get("error"):
        raise HTTP(403, "Discord login was denied.")

    state = request.query.get("state", "")
    expected_state = session.get("staff_oauth_state", "")
    if not state or not expected_state or not hmac.compare_digest(state, expected_state):
        raise HTTP(403, "Invalid OAuth state.")

    code = request.query.get("code", "")
    if not code:
        raise HTTP(400, "Missing OAuth code.")

    token_resp = requests.post(
        "https://discord.com/api/v10/oauth2/token",
        data={
            "client_id": settings.DISCORD_OAUTH_CLIENT_ID,
            "client_secret": settings.DISCORD_OAUTH_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.DISCORD_OAUTH_REDIRECT_URI,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=8,
    )
    if not token_resp.ok:
        raise HTTP(403, "Discord OAuth token exchange failed.")

    try:
        token_data = token_resp.json()
    except ValueError:
        raise HTTP(403, "Discord OAuth response was invalid.")

    access_token = (token_data.get("access_token") or "").strip()
    if not access_token:
        raise HTTP(403, "Discord OAuth token exchange failed.")

    identity = _get_discord_staff_identity(access_token)
    if not identity:
        _clear_staff_session()
        raise HTTP(403, "You do not have a valid staff role in this Discord server.")

    session["staff_auth"] = identity
    session.pop("staff_oauth_state", None)
    _sync_staff_member(identity)
    redirect(URL("staff"))


@action("staff/logout")
@action.uses(session)
def staff_logout():
    _clear_staff_session()
    redirect(URL("index"))


@action("staff", method=["GET", "POST"])
@action.uses("staff.html", db, session, T)
def staff_portal():
    auth = _require_staff("viewer", redirect_on_missing=True)
    flash = ""
    errors = {}
    actor_rank = _access_rank(auth["access_level"])

    if request.method == "POST":
        form_action = (request.forms.get("action") or "").strip()

        if form_action == "create_document":
            if actor_rank < _access_rank("editor"):
                raise HTTP(403, "Only editor-level staff can write documents.")
            title = (request.forms.get("title") or "").strip()
            markdown = (request.forms.get("markdown") or "").strip()
            read_level = _normalize_staff_level(request.forms.get("read_level"), default="viewer")
            write_level = _normalize_staff_level(request.forms.get("write_level"), default="editor")
            if not title:
                errors["document"] = "Document title is required."
            elif not markdown:
                errors["document"] = "Document markdown is required."
            elif actor_rank < max(_access_rank(read_level), _access_rank(write_level)):
                errors["document"] = "You cannot create a document above your permission level."
            else:
                slug = _slugify(title)
                while db(db.staff_document.slug == slug).count():
                    slug = f"{slug}-{secrets.token_hex(2)}"
                db.staff_document.insert(
                    title=title,
                    slug=slug,
                    markdown=markdown,
                    read_level=read_level,
                    write_level=write_level,
                    created_by_discord_id=auth["discord_id"],
                    updated_by_discord_id=auth["discord_id"],
                )
                db.commit()
                flash = "Staff document saved."

        elif form_action == "update_document":
            try:
                doc_id = int(request.forms.get("document_id") or 0)
            except (TypeError, ValueError):
                doc_id = 0
            row = db(db.staff_document.id == doc_id).select().first()
            if not row:
                errors["document"] = "Document not found."
            elif actor_rank < _access_rank(row.write_level):
                raise HTTP(403, "You do not have write access to this document.")
            else:
                title = (request.forms.get("title") or "").strip()
                markdown = (request.forms.get("markdown") or "").strip()
                read_level = _normalize_staff_level(request.forms.get("read_level"), default=row.read_level)
                write_level = _normalize_staff_level(request.forms.get("write_level"), default=row.write_level)
                if not title or not markdown:
                    errors["document"] = "Document title and markdown are required."
                elif actor_rank < max(_access_rank(read_level), _access_rank(write_level)):
                    errors["document"] = "You cannot set permission levels above your own."
                else:
                    db(db.staff_document.id == doc_id).update(
                        title=title,
                        markdown=markdown,
                        read_level=read_level,
                        write_level=write_level,
                        updated_by_discord_id=auth["discord_id"],
                        updated_on=_dt.datetime.now(_dt.timezone.utc),
                    )
                    db.commit()
                    flash = "Staff document updated."

        elif form_action == "create_notification":
            if actor_rank < _access_rank("admin"):
                raise HTTP(403, "Only admin-level staff can post notifications.")
            message = (request.forms.get("message") or "").strip()
            if not message:
                errors["notification"] = "Notification message is required."
            else:
                db.staff_notification.insert(
                    message=message,
                    created_by_discord_id=auth["discord_id"],
                )
                db.commit()
                flash = "Notification posted."

        elif form_action == "create_note":
            if actor_rank < _access_rank("editor"):
                raise HTTP(403, "Only editor-level staff can create notes.")
            target_discord_id = (request.forms.get("target_discord_id") or "").strip()
            message = (request.forms.get("message") or "").strip()
            target = db(db.staff_member.discord_id == target_discord_id).select().first()
            if not target:
                errors["note"] = "Select a valid staff member."
            elif not message:
                errors["note"] = "Note message is required."
            elif not _can_manage_staff_note(auth["access_level"], target.access_level):
                raise HTTP(403, "You can only leave notes for staff below your own level.")
            else:
                db.staff_note.insert(
                    target_discord_id=target_discord_id,
                    message=message,
                    created_by_discord_id=auth["discord_id"],
                )
                db.commit()
                flash = "Staff note added."

    documents = db(db.staff_document).select(orderby=~db.staff_document.updated_on)
    visible_docs = [
        row for row in documents if actor_rank >= _access_rank(row.read_level)
    ]
    editable_doc_ids = {
        row.id for row in visible_docs if actor_rank >= _access_rank(row.write_level)
    }
    notifications = db(db.staff_notification).select(
        orderby=~db.staff_notification.created_on,
        limitby=(0, 50),
    )
    notes = db(db.staff_note.target_discord_id == auth["discord_id"]).select(
        orderby=~db.staff_note.created_on,
        limitby=(0, 50),
    )
    members = db(db.staff_member).select(orderby=db.staff_member.discord_username)
    member_names = {row.discord_id: row.discord_username for row in members}
    member_levels = {row.discord_id: row.access_level for row in members}

    return _ctx(
        staff_auth=auth,
        flash=flash,
        errors=errors,
        access_levels=list(_STAFF_LEVEL_RANKS.keys()),
        documents=[_serialize_staff_document(row) for row in visible_docs],
        editable_doc_ids=editable_doc_ids,
        notifications=[_serialize_staff_notification(row) for row in notifications],
        notes=[_serialize_staff_note(row) for row in notes],
        staff_members=[{"discord_id": row.discord_id, "discord_username": row.discord_username, "access_level": row.access_level} for row in members],
        member_names=member_names,
        member_levels=member_levels,
        can_edit_documents=actor_rank >= _access_rank("editor"),
        can_post_notifications=actor_rank >= _access_rank("admin"),
        can_create_notes=actor_rank >= _access_rank("editor"),
    )


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


def _read_json_body() -> dict:
    try:
        body = json.loads(request.body.read())
    except (ValueError, AttributeError):
        raise HTTP(400, "Invalid JSON body")
    if not isinstance(body, dict):
        raise HTTP(400, "JSON body must be an object")
    return body


def _decision_message_block(message: str) -> str:
    note = (message or "").strip()
    if not note:
        return ""
    return f"\n\nMessage from staff:\n{note}"


def _serialize_appeal(row) -> dict:
    return {
        "id": row.id,
        "appeal_type": row.appeal_type,
        "minecraft_username": row.minecraft_username,
        "discord_username": row.discord_username,
        "email": row.email or "",
        "reason": row.reason or "",
        "punishment_reason": row.punishment_reason or "",
        "why_unban": row.why_unban or "",
        "status": row.status,
        "discord_message_id": row.discord_message_id or "",
        "submitted_on": row.submitted_on.isoformat() if row.submitted_on else "",
    }


def _serialize_staff_application(row) -> dict:
    return {
        "id": row.id,
        "role": row.role,
        "minecraft_username": row.minecraft_username,
        "discord_username": row.discord_username,
        "email": row.email or "",
        "age": row.age,
        "timezone": row.timezone,
        "hours_per_week": row.hours_per_week,
        "why_apply": row.why_apply or "",
        "prior_experience": row.prior_experience or "",
        "anything_else": row.anything_else or "",
        "status": row.status,
        "discord_message_id": row.discord_message_id or "",
        "submitted_on": row.submitted_on.isoformat() if row.submitted_on else "",
    }


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

    body = _read_json_body()

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

    body = _read_json_body()

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


_APPEAL_STATUSES = {"open", "approved", "denied", "closed"}
_STAFF_APP_STATUSES = {"pending", "accepted", "rejected", "on_hold"}


@action("api/appeals", method=["GET"])
def api_appeals_list():
    """List appeals. Requires X-Roadmap-Secret header."""
    _check_roadmap_auth()
    response.headers["Content-Type"] = "application/json"
    response.headers["Cache-Control"] = "no-store"

    status = request.params.get("status", "open").strip().lower()
    if status and status not in _APPEAL_STATUSES:
        raise HTTP(400, f"status must be one of: {', '.join(sorted(_APPEAL_STATUSES))}")

    query = db.appeal
    if status:
        query = query.status == status

    rows = db(query).select(orderby=~db.appeal.submitted_on)
    return json.dumps([_serialize_appeal(row) for row in rows])


@action("api/appeals/<appeal_id:int>", method=["PATCH"])
def api_appeal_update(appeal_id):
    """Update an appeal status. Requires X-Roadmap-Secret header."""
    _check_roadmap_auth()
    response.headers["Content-Type"] = "application/json"

    row = db(db.appeal.id == appeal_id).select().first()
    if not row:
        raise HTTP(404, f"Appeal {appeal_id} not found")

    body = _read_json_body()
    status = (body.get("status") or "").strip().lower()
    decision_message = _decision_message_block(body.get("message") or "")
    if status not in _APPEAL_STATUSES:
        raise HTTP(400, f"status must be one of: {', '.join(sorted(_APPEAL_STATUSES))}")

    previous_status = row.status
    db(db.appeal.id == appeal_id).update(status=status)
    db.commit()
    row = db(db.appeal.id == appeal_id).select().first()

    if status in {"approved", "denied"} and status != previous_status:
        appeal_label = {"ban": "ban", "mute": "mute", "discord": "Discord"}.get(
            row.appeal_type, row.appeal_type
        )
        if status == "approved":
            _send_discord_dm(
                settings.DISCORD_BOT_TOKEN,
                row.discord_username,
                (
                    f"✅ Good news, **{row.minecraft_username}**! "
                    f"Your {appeal_label} appeal (#{appeal_id}) on Cobblemon Conquest has been "
                    f"**approved**. You are welcome back – see you in-game! "
                    f"If you have any questions, join us on Discord: "
                    f"{settings.DISCORD_INVITE_URL}"
                    f"{decision_message}"
                ),
            )

        _send_email(
            row.email,
            f"Cobblemon Conquest: Appeal #{appeal_id} {status}",
            (
                f"Hi {row.minecraft_username},\n\n"
                f"Your {appeal_label} appeal (ID #{appeal_id}) has been {status}.\n"
                f"If you have questions, join our Discord: {settings.DISCORD_INVITE_URL}"
                f"{decision_message}\n\n"
                "Regards,\nCobblemon Conquest Staff"
            ),
        )

    return json.dumps(_serialize_appeal(row))


@action("api/staff-applications", method=["GET"])
def api_staff_applications_list():
    """List staff applications. Requires X-Roadmap-Secret header."""
    _check_roadmap_auth()
    response.headers["Content-Type"] = "application/json"
    response.headers["Cache-Control"] = "no-store"

    status = request.params.get("status", "pending").strip().lower()
    if status and status not in _STAFF_APP_STATUSES:
        raise HTTP(400, f"status must be one of: {', '.join(sorted(_STAFF_APP_STATUSES))}")

    query = db.staff_application
    if status:
        query = query.status == status

    rows = db(query).select(orderby=~db.staff_application.submitted_on)
    return json.dumps([_serialize_staff_application(row) for row in rows])


@action("api/staff-applications/<application_id:int>", method=["PATCH"])
def api_staff_application_update(application_id):
    """Update a staff application status. Requires X-Roadmap-Secret header."""
    _check_roadmap_auth()
    response.headers["Content-Type"] = "application/json"

    row = db(db.staff_application.id == application_id).select().first()
    if not row:
        raise HTTP(404, f"Staff application {application_id} not found")

    body = _read_json_body()
    status = (body.get("status") or "").strip().lower()
    decision_message = _decision_message_block(body.get("message") or "")
    if status not in _STAFF_APP_STATUSES:
        raise HTTP(400, f"status must be one of: {', '.join(sorted(_STAFF_APP_STATUSES))}")

    previous_status = row.status
    db(db.staff_application.id == application_id).update(status=status)
    db.commit()
    row = db(db.staff_application.id == application_id).select().first()

    if status in {"accepted", "rejected"} and status != previous_status:
        if status == "accepted":
            _send_discord_dm(
                settings.DISCORD_BOT_TOKEN,
                row.discord_username,
                (
                    f"🎉 Congratulations, **{row.minecraft_username}**! "
                    f"Your staff application for the **{row.role}** role on "
                    f"Cobblemon Conquest has been **accepted**. "
                    f"Welcome to the team! Please check the Discord server for next steps: "
                    f"{settings.DISCORD_INVITE_URL}"
                    f"{decision_message}"
                ),
            )

        _send_email(
            row.email,
            f"Cobblemon Conquest: Staff application #{application_id} {status}",
            (
                f"Hi {row.minecraft_username},\n\n"
                f"Your staff application for the {row.role} role (ID #{application_id}) "
                f"has been {status}.\n"
                f"If you have questions, join our Discord: {settings.DISCORD_INVITE_URL}"
                f"{decision_message}\n\n"
                "Regards,\nCobblemon Conquest Staff"
            ),
        )

    return json.dumps(_serialize_staff_application(row))


# ── FAQ management API ────────────────────────────────────────────────────
# Called by the Discord bot.  Requires the X-Roadmap-Secret header.


def _serialize_faq_item(row) -> dict:
    return {
        "id": row.id,
        "question": row.question,
        "answer": row.answer or "",
        "category": row.category or "General",
        "sort_order": row.sort_order,
    }


@action("api/faq", method=["GET"])
def api_faq_list():
    """Return all FAQ items as JSON (public, no auth required)."""
    response.headers["Content-Type"] = "application/json"
    response.headers["Cache-Control"] = "no-store"
    items = db(db.faq_item).select(
        orderby=db.faq_item.sort_order | db.faq_item.created_on
    )
    return json.dumps([_serialize_faq_item(row) for row in items])


@action("api/faq", method=["POST"])
def api_faq_create():
    """Create a new FAQ item.  Requires X-Roadmap-Secret header."""
    _check_roadmap_auth()
    response.headers["Content-Type"] = "application/json"

    body = _read_json_body()

    question = (body.get("question") or "").strip()
    if not question:
        raise HTTP(400, "question is required")

    answer = (body.get("answer") or "").strip()
    if not answer:
        raise HTTP(400, "answer is required")

    category = (body.get("category") or "General").strip()
    sort_order = body.get("sort_order", 0)
    try:
        sort_order = int(sort_order)
    except (TypeError, ValueError):
        sort_order = 0

    new_id = db.faq_item.insert(
        question=question,
        answer=answer,
        category=category,
        sort_order=sort_order,
    )
    db.commit()
    row = db(db.faq_item.id == new_id).select().first()
    return json.dumps(_serialize_faq_item(row))


@action("api/faq/<item_id:int>", method=["PATCH"])
def api_faq_update(item_id):
    """Update an existing FAQ item.  Requires X-Roadmap-Secret header."""
    _check_roadmap_auth()
    response.headers["Content-Type"] = "application/json"

    row = db(db.faq_item.id == item_id).select().first()
    if not row:
        raise HTTP(404, f"FAQ item {item_id} not found")

    body = _read_json_body()

    updates = {}
    if "question" in body:
        question = (body["question"] or "").strip()
        if not question:
            raise HTTP(400, "question cannot be empty")
        updates["question"] = question
    if "answer" in body:
        answer = (body["answer"] or "").strip()
        if not answer:
            raise HTTP(400, "answer cannot be empty")
        updates["answer"] = answer
    if "category" in body:
        updates["category"] = (body["category"] or "General").strip()
    if "sort_order" in body:
        try:
            updates["sort_order"] = int(body["sort_order"])
        except (TypeError, ValueError):
            raise HTTP(400, "sort_order must be an integer")

    if not updates:
        raise HTTP(400, "No valid fields provided for update")

    db(db.faq_item.id == item_id).update(**updates)
    db.commit()
    row = db(db.faq_item.id == item_id).select().first()
    return json.dumps(_serialize_faq_item(row))


@action("api/faq/<item_id:int>", method=["DELETE"])
def api_faq_delete(item_id):
    """Delete a FAQ item.  Requires X-Roadmap-Secret header."""
    _check_roadmap_auth()
    response.headers["Content-Type"] = "application/json"

    row = db(db.faq_item.id == item_id).select().first()
    if not row:
        raise HTTP(404, f"FAQ item {item_id} not found")

    db(db.faq_item.id == item_id).delete()
    db.commit()
    return json.dumps({"deleted": item_id})


# ── Staff portal API ────────────────────────────────────────────────────────

@action("api/staff/session", method=["GET"])
def api_staff_session():
    response.headers["Content-Type"] = "application/json"
    auth = _require_staff("viewer")
    return json.dumps(
        {
            "discord_id": auth["discord_id"],
            "discord_username": auth["discord_username"],
            "access_level": auth["access_level"],
            "discord_roles": auth.get("discord_roles", []),
        }
    )


@action("api/staff/documents", method=["GET"])
def api_staff_documents_list():
    response.headers["Content-Type"] = "application/json"
    auth = _require_staff("viewer")
    actor_rank = _access_rank(auth["access_level"])
    rows = db(db.staff_document).select(orderby=~db.staff_document.updated_on)
    visible_rows = [row for row in rows if actor_rank >= _access_rank(row.read_level)]
    return json.dumps([_serialize_staff_document(row) for row in visible_rows])


@action("api/staff/notifications", method=["GET"])
def api_staff_notifications_list():
    response.headers["Content-Type"] = "application/json"
    _require_staff("viewer")
    rows = db(db.staff_notification).select(orderby=~db.staff_notification.created_on, limitby=(0, 100))
    return json.dumps([_serialize_staff_notification(row) for row in rows])


@action("api/staff/notes", method=["GET"])
def api_staff_notes_list():
    response.headers["Content-Type"] = "application/json"
    auth = _require_staff("viewer")
    rows = db(db.staff_note.target_discord_id == auth["discord_id"]).select(
        orderby=~db.staff_note.created_on,
        limitby=(0, 100),
    )
    return json.dumps([_serialize_staff_note(row) for row in rows])
