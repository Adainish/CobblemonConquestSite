"""
Cobblemon Conquest – roadmap, FAQ, and moderation management Discord bot.

Slash commands (all require a role listed in config.ALLOWED_ROLES):
  /roadmap list                     – list all roadmap items
  /roadmap add <title> [description] [status] [sort_order]
                                    – add a new item
  /roadmap edit <id> [title] [description] [status] [sort_order]
                                    – edit an existing item
  /roadmap remove <id>              – delete an item
  /faq list                         – list all FAQ items
  /faq add <question> <answer> [category] [sort_order]
                                    – add a new FAQ entry
  /faq edit <id> [question] [answer] [category] [sort_order]
                                    – edit an existing FAQ entry
  /faq remove <id>                  – delete a FAQ entry
  /appeals list [status]            – list punishment appeals by status
  /appeals vote <id> <decision>     – set appeal outcome
  /appeals force-accept <id>        – force set appeal to approved
  /applications list [status]       – list staff applications by status
  /applications vote <id> <decision>– set staff application outcome
  /applications force-accept <id>   – force set application to accepted

Setup:
  1. Copy config.example.py → config.py and fill in your values.
  2. pip install -r requirements.txt
  3. python bot.py
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile

import aiohttp
import discord
from discord import app_commands

try:
    import config
except ModuleNotFoundError as exc:
    raise SystemExit(
        "config.py not found. Copy config.example.py to config.py and fill in your values."
    ) from exc

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("roadmap-bot")

VALID_STATUSES = ["planned", "in_progress", "completed", "cancelled"]
_ROADMAP_CHANGE_ACTION_LABELS = {
    "added": "added",
    "updated": "updated",
    "removed": "removed",
}
_DEFAULT_ROADMAP_CHANGE_MESSAGE = (
    "🗺️ Roadmap {action_label}: **#{item_id}** {title}\n{roadmap_url}"
)


# ── HTTP helpers ──────────────────────────────────────────────────────────

def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Roadmap-Secret": config.ROADMAP_API_SECRET,
    }


async def _parse_response(resp) -> dict | list:
    """Return parsed JSON body, or a dict with an 'error' key if the response
    is not JSON (e.g. an HTML error page returned by the web framework)."""
    content_type = resp.headers.get("Content-Type", "")
    if "application/json" in content_type:
        return await resp.json()
    text = await resp.text()
    return {"error": text[:300]}


async def _get(path: str) -> tuple[int, dict | list]:
    """Send a GET request to the site API and return (status_code, body)."""
    url = f"{config.SITE_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=_headers()) as resp:
            return resp.status, await _parse_response(resp)


async def _post(path: str, payload: dict) -> tuple[int, dict]:
    url = f"{config.SITE_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=_headers()) as resp:
            return resp.status, await _parse_response(resp)


async def _patch(path: str, payload: dict) -> tuple[int, dict]:
    url = f"{config.SITE_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    async with aiohttp.ClientSession() as session:
        async with session.patch(url, json=payload, headers=_headers()) as resp:
            return resp.status, await _parse_response(resp)


async def _delete(path: str) -> tuple[int, dict]:
    url = f"{config.SITE_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    async with aiohttp.ClientSession() as session:
        async with session.delete(url, headers=_headers()) as resp:
            return resp.status, await _parse_response(resp)


# ── Permission guard ──────────────────────────────────────────────────────

def _has_permission(interaction: discord.Interaction) -> bool:
    """Return True if the interaction member has at least one allowed role."""
    member = interaction.user
    if not hasattr(member, "roles"):
        return False
    allowed_lower = {r.lower() for r in config.ALLOWED_ROLES}
    return any(role.name.lower() in allowed_lower for role in member.roles)


# ── Status badge helper ───────────────────────────────────────────────────

_STATUS_EMOJI = {
    "planned": "🗺️",
    "in_progress": "🔨",
    "completed": "✅",
    "cancelled": "❌",
}


def _fmt_status(status: str) -> str:
    emoji = _STATUS_EMOJI.get(status, "❓")
    return f"{emoji} {status.replace('_', ' ').title()}"


def _shorten(value: str, max_len: int = 100) -> str:
    if len(value) <= max_len:
        return value
    return value[: max_len - 1] + "…"


class _SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _roadmap_url() -> str:
    return f"{config.SITE_BASE_URL.rstrip('/')}/roadmap"


def _roadmap_change_channel_id() -> int:
    raw_channel_id = getattr(config, "ROADMAP_CHANGE_CHANNEL_ID", 0) or 0
    try:
        return int(raw_channel_id)
    except (TypeError, ValueError):
        logger.warning("ROADMAP_CHANGE_CHANNEL_ID is invalid: %r", raw_channel_id)
        return 0


def _roadmap_change_notifications_enabled() -> bool:
    return _roadmap_change_channel_id() > 0


def _format_roadmap_change_message(action: str, item: dict) -> str:
    template = (
        getattr(config, "ROADMAP_CHANGE_MESSAGE", _DEFAULT_ROADMAP_CHANGE_MESSAGE)
        or _DEFAULT_ROADMAP_CHANGE_MESSAGE
    )
    status = item.get("status", "")
    values = _SafeFormatDict(
        action=action,
        action_label=_ROADMAP_CHANGE_ACTION_LABELS.get(action, action),
        description=item.get("description", ""),
        item_id=item.get("id", ""),
        roadmap_url=_roadmap_url(),
        status=status,
        status_label=_fmt_status(status) if status else "",
        title=item.get("title", ""),
    )
    try:
        return template.format_map(values).strip()
    except (AttributeError, ValueError):
        return _DEFAULT_ROADMAP_CHANGE_MESSAGE.format_map(values).strip()


async def _get_roadmap_item(item_id: int) -> dict | None:
    status_code, body = await _get("api/roadmap")
    if status_code != 200 or not isinstance(body, list):
        return None
    for item in body:
        if item.get("id") == item_id:
            return item
    return None


async def _capture_roadmap_screenshot() -> str | None:
    try:
        from playwright.async_api import Error as PlaywrightError, async_playwright
    except ModuleNotFoundError:
        logger.warning("Playwright is not installed; skipping roadmap screenshot.")
        return None

    screenshot = tempfile.NamedTemporaryFile(
        prefix="roadmap-change-", suffix=".png", delete=False
    )
    screenshot.close()
    browser = None
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page(viewport={"width": 1440, "height": 2200})
            await page.goto(_roadmap_url(), wait_until="networkidle")
            await page.locator("main").screenshot(path=screenshot.name)
    except (PlaywrightError, OSError) as exc:
        logger.warning("Failed to capture roadmap screenshot: %s", exc)
        with contextlib.suppress(OSError):
            os.unlink(screenshot.name)
        return None
    finally:
        if browser is not None:
            with contextlib.suppress(Exception):
                await browser.close()
    return screenshot.name


async def _resolve_roadmap_notification_channel(
    client: discord.Client,
) -> discord.abc.Messageable | None:
    channel_id = _roadmap_change_channel_id()
    if channel_id <= 0:
        return None
    channel = client.get_channel(channel_id)
    if channel is not None:
        return channel
    try:
        fetched_channel = await client.fetch_channel(channel_id)
    except (discord.DiscordException, TypeError, ValueError) as exc:
        logger.warning(
            "Failed to resolve roadmap change notification channel %s: %s",
            channel_id,
            exc,
        )
        return None
    return fetched_channel if hasattr(fetched_channel, "send") else None


async def _send_roadmap_change_notification(
    client: discord.Client, action: str, item: dict
) -> None:
    channel = await _resolve_roadmap_notification_channel(client)
    if channel is None:
        return

    message = _format_roadmap_change_message(action, item)
    screenshot_path = await _capture_roadmap_screenshot()
    try:
        if screenshot_path:
            await channel.send(
                content=message,
                file=discord.File(
                    screenshot_path,
                    filename=f"roadmap-{action}-{item.get('id', 'snapshot')}.png",
                ),
            )
        else:
            await channel.send(content=message)
    finally:
        if screenshot_path:
            with contextlib.suppress(OSError):
                os.unlink(screenshot_path)


def _log_notification_task_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except Exception:
        logger.exception("Roadmap change notification task failed.")


def _schedule_roadmap_change_notification(
    client: discord.Client, action: str, item: dict
) -> None:
    if not _roadmap_change_notifications_enabled():
        return
    task = asyncio.create_task(_send_roadmap_change_notification(client, action, item))
    task.add_done_callback(_log_notification_task_result)


# ── Bot setup ─────────────────────────────────────────────────────────────

class RoadmapBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        await self.tree.sync()
        logger.info("Slash commands synced globally.")

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (%s)", self.user, self.user.id)


bot = RoadmapBot()

# ── /roadmap command group ────────────────────────────────────────────────

roadmap_group = app_commands.Group(
    name="roadmap",
    description="Manage the Cobblemon Conquest development roadmap.",
)


@roadmap_group.command(name="list", description="List all current roadmap items.")
async def roadmap_list(interaction: discord.Interaction) -> None:
    if not _has_permission(interaction):
        await interaction.response.send_message(
            "❌ You don't have permission to use roadmap commands.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    status_code, body = await _get("api/roadmap")

    if status_code != 200 or not isinstance(body, list):
        await interaction.followup.send(
            f"⚠️ Failed to fetch roadmap items (HTTP {status_code}).", ephemeral=True
        )
        return

    if not body:
        await interaction.followup.send("The roadmap is empty.", ephemeral=True)
        return

    lines = [f"**Roadmap Items** ({len(body)} total)\n"]
    for item in body:
        lines.append(
            f"**#{item['id']}** – {item['title']}\n"
            f"  Status: {_fmt_status(item['status'])}"
            + (f"\n  {item['description']}" if item.get("description") else "")
        )
    await interaction.followup.send("\n".join(lines), ephemeral=True)


@roadmap_group.command(name="add", description="Add a new roadmap item.")
@app_commands.describe(
    title="Title of the roadmap item.",
    description="Optional description.",
    status="Item status (planned / in_progress / completed / cancelled).",
    sort_order="Display order (lower numbers appear first).",
)
@app_commands.choices(
    status=[app_commands.Choice(name=s.replace("_", " ").title(), value=s) for s in VALID_STATUSES]
)
async def roadmap_add(
    interaction: discord.Interaction,
    title: str,
    description: str = "",
    status: str = "planned",
    sort_order: int = 0,
) -> None:
    if not _has_permission(interaction):
        await interaction.response.send_message(
            "❌ You don't have permission to use roadmap commands.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    payload = {
        "title": title,
        "description": description,
        "status": status,
        "sort_order": sort_order,
    }
    status_code, body = await _post("api/roadmap", payload)

    if status_code == 200:
        await interaction.followup.send(
            f"✅ Added roadmap item **#{body['id']}**: {body['title']} [{_fmt_status(body['status'])}]",
            ephemeral=True,
        )
        _schedule_roadmap_change_notification(interaction.client, "added", body)
    else:
        error = body.get("message") or body.get("error") or str(body)
        await interaction.followup.send(
            f"⚠️ Failed to add item (HTTP {status_code}): {error}", ephemeral=True
        )


@roadmap_group.command(name="edit", description="Edit an existing roadmap item.")
@app_commands.describe(
    item_id="The numeric ID of the roadmap item to edit.",
    title="New title (leave blank to keep current).",
    description="New description (leave blank to keep current).",
    status="New status (leave blank to keep current).",
    sort_order="New sort order (use -1 to keep current).",
)
@app_commands.choices(
    status=[app_commands.Choice(name=s.replace("_", " ").title(), value=s) for s in VALID_STATUSES]
)
async def roadmap_edit(
    interaction: discord.Interaction,
    item_id: int,
    title: str = "",
    description: str = "",
    status: str = "",
    sort_order: int = -1,
) -> None:
    if not _has_permission(interaction):
        await interaction.response.send_message(
            "❌ You don't have permission to use roadmap commands.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    payload: dict = {}
    if title:
        payload["title"] = title
    if description:
        payload["description"] = description
    if status:
        payload["status"] = status
    if sort_order != -1:
        payload["sort_order"] = sort_order

    if not payload:
        await interaction.followup.send(
            "ℹ️ Nothing to update – provide at least one field to change.", ephemeral=True
        )
        return

    status_code, body = await _patch(f"api/roadmap/{item_id}", payload)

    if status_code == 200:
        await interaction.followup.send(
            f"✅ Updated roadmap item **#{body['id']}**: {body['title']} [{_fmt_status(body['status'])}]",
            ephemeral=True,
        )
        _schedule_roadmap_change_notification(interaction.client, "updated", body)
    elif status_code == 404:
        await interaction.followup.send(
            f"⚠️ Roadmap item #{item_id} not found.", ephemeral=True
        )
    else:
        error = body.get("message") or body.get("error") or str(body)
        await interaction.followup.send(
            f"⚠️ Failed to update item (HTTP {status_code}): {error}", ephemeral=True
        )


@roadmap_group.command(name="remove", description="Remove a roadmap item.")
@app_commands.describe(item_id="The numeric ID of the roadmap item to remove.")
async def roadmap_remove(interaction: discord.Interaction, item_id: int) -> None:
    if not _has_permission(interaction):
        await interaction.response.send_message(
            "❌ You don't have permission to use roadmap commands.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    previous_item = None
    if _roadmap_change_notifications_enabled():
        previous_item = await _get_roadmap_item(item_id)
    status_code, body = await _delete(f"api/roadmap/{item_id}")

    if status_code == 200:
        await interaction.followup.send(
            f"✅ Deleted roadmap item **#{body['deleted']}**.", ephemeral=True
        )
        _schedule_roadmap_change_notification(
            interaction.client,
            "removed",
            previous_item or {"id": body["deleted"]},
        )
    elif status_code == 404:
        await interaction.followup.send(
            f"⚠️ Roadmap item #{item_id} not found.", ephemeral=True
        )
    else:
        error = body.get("message") or body.get("error") or str(body)
        await interaction.followup.send(
            f"⚠️ Failed to delete item (HTTP {status_code}): {error}", ephemeral=True
        )


bot.tree.add_command(roadmap_group)


# ── /faq command group ─────────────────────────────────────────────────────

faq_group = app_commands.Group(
    name="faq",
    description="Manage the Cobblemon Conquest FAQ page.",
)


@faq_group.command(name="list", description="List all current FAQ items.")
async def faq_list(interaction: discord.Interaction) -> None:
    if not _has_permission(interaction):
        await interaction.response.send_message(
            "❌ You don't have permission to use FAQ commands.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    status_code, body = await _get("api/faq")

    if status_code != 200 or not isinstance(body, list):
        await interaction.followup.send(
            f"⚠️ Failed to fetch FAQ items (HTTP {status_code}).", ephemeral=True
        )
        return

    if not body:
        await interaction.followup.send("The FAQ is empty.", ephemeral=True)
        return

    lines = [f"**FAQ Items** ({len(body)} total)\n"]
    for item in body:
        lines.append(
            f"**#{item['id']}** [{item['category']}] {_shorten(item['question'], 80)}\n"
            f"  {_shorten(item['answer'], 100)}"
        )
    await interaction.followup.send("\n".join(lines), ephemeral=True)


@faq_group.command(name="add", description="Add a new FAQ entry.")
@app_commands.describe(
    question="The question to display.",
    answer="The answer to the question.",
    category="Category grouping (default: General).",
    sort_order="Display order (lower numbers appear first).",
)
async def faq_add(
    interaction: discord.Interaction,
    question: str,
    answer: str,
    category: str = "General",
    sort_order: int = 0,
) -> None:
    if not _has_permission(interaction):
        await interaction.response.send_message(
            "❌ You don't have permission to use FAQ commands.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    payload = {
        "question": question,
        "answer": answer,
        "category": category,
        "sort_order": sort_order,
    }
    status_code, body = await _post("api/faq", payload)

    if status_code == 200:
        await interaction.followup.send(
            f"✅ Added FAQ item **#{body['id']}**: {_shorten(body['question'], 80)}",
            ephemeral=True,
        )
    else:
        error = body.get("message") or body.get("error") or str(body)
        await interaction.followup.send(
            f"⚠️ Failed to add FAQ item (HTTP {status_code}): {error}", ephemeral=True
        )


@faq_group.command(name="edit", description="Edit an existing FAQ entry.")
@app_commands.describe(
    item_id="The numeric ID of the FAQ item to edit.",
    question="New question text (leave blank to keep current).",
    answer="New answer text (leave blank to keep current).",
    category="New category (leave blank to keep current).",
    sort_order="New sort order (use -1 to keep current).",
)
async def faq_edit(
    interaction: discord.Interaction,
    item_id: int,
    question: str = "",
    answer: str = "",
    category: str = "",
    sort_order: int = -1,
) -> None:
    if not _has_permission(interaction):
        await interaction.response.send_message(
            "❌ You don't have permission to use FAQ commands.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    payload: dict = {}
    if question:
        payload["question"] = question
    if answer:
        payload["answer"] = answer
    if category:
        payload["category"] = category
    if sort_order != -1:
        payload["sort_order"] = sort_order

    if not payload:
        await interaction.followup.send(
            "ℹ️ Nothing to update – provide at least one field to change.", ephemeral=True
        )
        return

    status_code, body = await _patch(f"api/faq/{item_id}", payload)

    if status_code == 200:
        await interaction.followup.send(
            f"✅ Updated FAQ item **#{body['id']}**: {_shorten(body['question'], 80)}",
            ephemeral=True,
        )
    elif status_code == 404:
        await interaction.followup.send(
            f"⚠️ FAQ item #{item_id} not found.", ephemeral=True
        )
    else:
        error = body.get("message") or body.get("error") or str(body)
        await interaction.followup.send(
            f"⚠️ Failed to update FAQ item (HTTP {status_code}): {error}", ephemeral=True
        )


@faq_group.command(name="remove", description="Remove a FAQ entry.")
@app_commands.describe(item_id="The numeric ID of the FAQ item to remove.")
async def faq_remove(interaction: discord.Interaction, item_id: int) -> None:
    if not _has_permission(interaction):
        await interaction.response.send_message(
            "❌ You don't have permission to use FAQ commands.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    status_code, body = await _delete(f"api/faq/{item_id}")

    if status_code == 200:
        await interaction.followup.send(
            f"✅ Deleted FAQ item **#{body['deleted']}**.", ephemeral=True
        )
    elif status_code == 404:
        await interaction.followup.send(
            f"⚠️ FAQ item #{item_id} not found.", ephemeral=True
        )
    else:
        error = body.get("message") or body.get("error") or str(body)
        await interaction.followup.send(
            f"⚠️ Failed to delete FAQ item (HTTP {status_code}): {error}", ephemeral=True
        )


bot.tree.add_command(faq_group)


# ── /appeals command group ─────────────────────────────────────────────────

appeals_group = app_commands.Group(
    name="appeals",
    description="Review punishment appeals submitted via the website.",
)

_APPEAL_STATUSES = ["open", "approved", "denied", "closed"]
_APPEAL_DECISIONS = {
    "approve": "approved",
    "deny": "denied",
    "close": "closed",
}


@appeals_group.command(name="list", description="List punishment appeals by status.")
@app_commands.describe(status="Appeal status to list.")
@app_commands.choices(
    status=[app_commands.Choice(name=s.title(), value=s) for s in _APPEAL_STATUSES]
)
async def appeals_list(
    interaction: discord.Interaction,
    status: str = "open",
) -> None:
    if not _has_permission(interaction):
        await interaction.response.send_message(
            "❌ You don't have permission to use appeals commands.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    status_code, body = await _get(f"api/appeals?status={status}")

    if status_code != 200 or not isinstance(body, list):
        await interaction.followup.send(
            f"⚠️ Failed to fetch appeals (HTTP {status_code}).", ephemeral=True
        )
        return

    if not body:
        await interaction.followup.send(
            f"No appeals with status **{status}**.", ephemeral=True
        )
        return

    lines = [f"**Appeals ({status})** – {len(body)} item(s)\n"]
    for item in body[:20]:
        lines.append(
            f"**#{item['id']}** [{item['appeal_type']}] "
            f"{item['minecraft_username']} ({item['discord_username']})\n"
            f"  {_shorten(item.get('why_unban') or 'No appeal reason provided.', 120)}"
        )
    if len(body) > 20:
        lines.append(f"\n…and {len(body) - 20} more.")

    await interaction.followup.send("\n".join(lines), ephemeral=True)


@appeals_group.command(name="vote", description="Set the outcome of an appeal.")
@app_commands.describe(
    appeal_id="The appeal ID shown in Discord/webhook messages.",
    decision="Vote decision.",
)
@app_commands.choices(
    decision=[
        app_commands.Choice(name="Approve", value="approve"),
        app_commands.Choice(name="Deny", value="deny"),
        app_commands.Choice(name="Close", value="close"),
    ]
)
async def appeals_vote(
    interaction: discord.Interaction,
    appeal_id: int,
    decision: str,
) -> None:
    if not _has_permission(interaction):
        await interaction.response.send_message(
            "❌ You don't have permission to use appeals commands.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    new_status = _APPEAL_DECISIONS[decision]
    status_code, body = await _patch(f"api/appeals/{appeal_id}", {"status": new_status})

    if status_code == 200:
        await interaction.followup.send(
            f"✅ Appeal **#{body['id']}** set to **{body['status']}**.", ephemeral=True
        )
    elif status_code == 404:
        await interaction.followup.send(
            f"⚠️ Appeal #{appeal_id} not found.", ephemeral=True
        )
    else:
        error = body.get("message") or body.get("error") or str(body)
        await interaction.followup.send(
            f"⚠️ Failed to update appeal (HTTP {status_code}): {error}",
            ephemeral=True,
        )


@appeals_group.command(name="force-accept", description="Force set an appeal outcome to approved.")
@app_commands.describe(
    appeal_id="The appeal ID shown in Discord/webhook messages.",
)
async def appeals_force_accept(
    interaction: discord.Interaction,
    appeal_id: int,
) -> None:
    if not _has_permission(interaction):
        await interaction.response.send_message(
            "❌ You don't have permission to use appeals commands.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    status_code, body = await _patch(f"api/appeals/{appeal_id}", {"status": "approved"})

    if status_code == 200:
        await interaction.followup.send(
            f"✅ Appeal **#{body['id']}** force-set to **{body['status']}**.",
            ephemeral=True,
        )
    elif status_code == 404:
        await interaction.followup.send(
            f"⚠️ Appeal #{appeal_id} not found.", ephemeral=True
        )
    else:
        error = body.get("message") or body.get("error") or str(body)
        await interaction.followup.send(
            f"⚠️ Failed to force-update appeal (HTTP {status_code}): {error}",
            ephemeral=True,
        )


bot.tree.add_command(appeals_group)


# ── /applications command group ────────────────────────────────────────────

applications_group = app_commands.Group(
    name="applications",
    description="Review staff applications submitted via the website.",
)

_APPLICATION_STATUSES = ["pending", "on_hold", "accepted", "rejected"]
_APPLICATION_DECISIONS = {
    "accept": "accepted",
    "reject": "rejected",
    "hold": "on_hold",
}


@applications_group.command(
    name="list",
    description="List staff applications by status.",
)
@app_commands.describe(status="Application status to list.")
@app_commands.choices(
    status=[app_commands.Choice(name=s.replace("_", " ").title(), value=s) for s in _APPLICATION_STATUSES]
)
async def applications_list(
    interaction: discord.Interaction,
    status: str = "pending",
) -> None:
    if not _has_permission(interaction):
        await interaction.response.send_message(
            "❌ You don't have permission to use applications commands.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    status_code, body = await _get(f"api/staff-applications?status={status}")

    if status_code != 200 or not isinstance(body, list):
        await interaction.followup.send(
            f"⚠️ Failed to fetch applications (HTTP {status_code}).", ephemeral=True
        )
        return

    if not body:
        await interaction.followup.send(
            f"No staff applications with status **{status}**.", ephemeral=True
        )
        return

    lines = [f"**Staff Applications ({status})** – {len(body)} item(s)\n"]
    for item in body[:20]:
        lines.append(
            f"**#{item['id']}** [{item['role']}] "
            f"{item['minecraft_username']} ({item['discord_username']})\n"
            f"  {_shorten(item.get('why_apply') or 'No application reason provided.', 120)}"
        )
    if len(body) > 20:
        lines.append(f"\n…and {len(body) - 20} more.")

    await interaction.followup.send("\n".join(lines), ephemeral=True)


@applications_group.command(
    name="vote",
    description="Set the outcome of a staff application.",
)
@app_commands.describe(
    application_id="The staff application ID shown in webhook messages.",
    decision="Vote decision.",
)
@app_commands.choices(
    decision=[
        app_commands.Choice(name="Accept", value="accept"),
        app_commands.Choice(name="Reject", value="reject"),
        app_commands.Choice(name="Put On Hold", value="hold"),
    ]
)
async def applications_vote(
    interaction: discord.Interaction,
    application_id: int,
    decision: str,
) -> None:
    if not _has_permission(interaction):
        await interaction.response.send_message(
            "❌ You don't have permission to use applications commands.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    new_status = _APPLICATION_DECISIONS[decision]
    status_code, body = await _patch(
        f"api/staff-applications/{application_id}",
        {"status": new_status},
    )

    if status_code == 200:
        await interaction.followup.send(
            f"✅ Staff application **#{body['id']}** set to **{body['status']}**.",
            ephemeral=True,
        )
    elif status_code == 404:
        await interaction.followup.send(
            f"⚠️ Staff application #{application_id} not found.", ephemeral=True
        )
    else:
        error = body.get("message") or body.get("error") or str(body)
        await interaction.followup.send(
            f"⚠️ Failed to update application (HTTP {status_code}): {error}",
            ephemeral=True,
        )


@applications_group.command(
    name="force-accept",
    description="Force set a staff application outcome to accepted.",
)
@app_commands.describe(
    application_id="The staff application ID shown in webhook messages.",
)
async def applications_force_accept(
    interaction: discord.Interaction,
    application_id: int,
) -> None:
    if not _has_permission(interaction):
        await interaction.response.send_message(
            "❌ You don't have permission to use applications commands.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    status_code, body = await _patch(
        f"api/staff-applications/{application_id}",
        {"status": "accepted"},
    )

    if status_code == 200:
        await interaction.followup.send(
            f"✅ Staff application **#{body['id']}** force-set to **{body['status']}**.",
            ephemeral=True,
        )
    elif status_code == 404:
        await interaction.followup.send(
            f"⚠️ Staff application #{application_id} not found.", ephemeral=True
        )
    else:
        error = body.get("message") or body.get("error") or str(body)
        await interaction.followup.send(
            f"⚠️ Failed to force-update application (HTTP {status_code}): {error}",
            ephemeral=True,
        )


bot.tree.add_command(applications_group)

if __name__ == "__main__":
    bot.run(config.BOT_TOKEN)
