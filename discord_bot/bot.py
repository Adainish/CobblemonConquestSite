"""
Cobblemon Conquest – roadmap and moderation management Discord bot.

Slash commands (all require a role listed in config.ALLOWED_ROLES):
  /roadmap list                     – list all roadmap items
  /roadmap add <title> [description] [status] [sort_order]
                                    – add a new item
  /roadmap edit <id> [title] [description] [status] [sort_order]
                                    – edit an existing item
  /roadmap remove <id>              – delete an item
  /appeals list [status]            – list punishment appeals by status
  /appeals vote <id> <decision>     – set appeal outcome
  /applications list [status]       – list staff applications by status
  /applications vote <id> <decision>– set staff application outcome

Setup:
  1. Copy config.example.py → config.py and fill in your values.
  2. pip install -r requirements.txt
  3. python bot.py
"""

from __future__ import annotations

import logging

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


# ── HTTP helpers ──────────────────────────────────────────────────────────

def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Roadmap-Secret": config.ROADMAP_API_SECRET,
    }


async def _get(path: str) -> tuple[int, dict | list]:
    """Send a GET request to the site API and return (status_code, body)."""
    url = f"{config.SITE_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=_headers()) as resp:
            return resp.status, await resp.json()


async def _post(path: str, payload: dict) -> tuple[int, dict]:
    url = f"{config.SITE_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=_headers()) as resp:
            return resp.status, await resp.json()


async def _patch(path: str, payload: dict) -> tuple[int, dict]:
    url = f"{config.SITE_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    async with aiohttp.ClientSession() as session:
        async with session.patch(url, json=payload, headers=_headers()) as resp:
            return resp.status, await resp.json()


async def _delete(path: str) -> tuple[int, dict]:
    url = f"{config.SITE_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    async with aiohttp.ClientSession() as session:
        async with session.delete(url, headers=_headers()) as resp:
            return resp.status, await resp.json()


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
    status_code, body = await _delete(f"api/roadmap/{item_id}")

    if status_code == 200:
        await interaction.followup.send(
            f"✅ Deleted roadmap item **#{body['deleted']}**.", ephemeral=True
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


bot.tree.add_command(applications_group)

if __name__ == "__main__":
    bot.run(config.BOT_TOKEN)
