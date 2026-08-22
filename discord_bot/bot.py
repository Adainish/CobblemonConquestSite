"""
Cobblemon Conquest – roadmap management Discord bot.

Slash commands (all require a role listed in config.ALLOWED_ROLES):
  /roadmap list                     – list all roadmap items
  /roadmap add <title> [description] [status] [sort_order]
                                    – add a new item
  /roadmap edit <id> [title] [description] [status] [sort_order]
                                    – edit an existing item
  /roadmap remove <id>              – delete an item

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

if __name__ == "__main__":
    bot.run(config.BOT_TOKEN)
