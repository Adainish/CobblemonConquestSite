"""
Configuration for the Cobblemon Conquest roadmap Discord bot.

Copy this file to config.py and fill in your values.
config.py is git-ignored – never commit real secrets.

Generate a secret key with:
    python -c "import secrets; print(secrets.token_hex(32))"
"""

# Discord bot token (from https://discord.com/developers/applications)
BOT_TOKEN: str = "YOUR_BOT_TOKEN_HERE"

# Base URL of the website (no trailing slash)
SITE_BASE_URL: str = "https://cobblemonconquest.com"

# Shared secret – must match ROADMAP_API_SECRET in the site's settings_private.py
ROADMAP_API_SECRET: str = "CHANGE_ME_TO_A_LONG_RANDOM_SECRET"

# Discord role names (case-insensitive) whose members may use roadmap commands.
# Members with ANY of these roles are granted access.
ALLOWED_ROLES: list[str] = ["Admin", "Owner", "Staff Manager"]
