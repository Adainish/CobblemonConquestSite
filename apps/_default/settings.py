"""
App-level settings for Cobblemon Conquest.
Override sensitive values in settings_private.py (git-ignored).
"""

import os

from py4web.core import required_folder

APP_FOLDER = os.path.dirname(__file__)
APP_NAME = os.path.split(APP_FOLDER)[-1]

DB_FOLDER = required_folder(APP_FOLDER, "databases")
DB_URI = "sqlite://storage.db"
DB_POOL_SIZE = 1
DB_MIGRATE = True
DB_FAKE_MIGRATE = False

STATIC_FOLDER = required_folder(APP_FOLDER, "static")
UPLOAD_FOLDER = required_folder(APP_FOLDER, "uploads")

# Sessions via signed cookies (no server-side store needed)
SESSION_TYPE = "cookies"
SESSION_SECRET_KEY = None  # Set in settings_private.py

LOGGERS = ["warning:stdout"]

T_FOLDER = required_folder(APP_FOLDER, "translations")

# Disable auth features – we manage our own forms
VERIFY_EMAIL = False
REQUIRES_APPROVAL = False
LOGIN_AFTER_REGISTRATION = False
PASSWORD_ENTROPY = 0
ALLOWED_ACTIONS = []
SMTP_SERVER = None
SMTP_SENDER = "noreply@cobblemonconquest.com"
SMTP_LOGIN = ""
SMTP_SSL = False
SMTP_TLS = False
DEFAULT_LOGIN_ENABLED = False

# ── Conquest-specific settings ─────────────────────────────────────────────
SERVER_IP = "play.cobblemonconquest.com"
STORE_URL = "https://store.cobblemonconquest.com"
MODPACK_URL = "https://www.curseforge.com/minecraft/modpacks/cobblemon-conquest"
MODRINTH_URL = "https://modrinth.com/modpack/cobblemon-conquest"
DISCORD_INVITE_URL = "https://discord.cobblemonconquest.com"

VOTE_SITES = [
    {"name": "Minecraft Server List", "url": "https://minecraft-server-list.com/server/cobblemon-conquest/vote/"},
    {"name": "Planet Minecraft", "url": "https://www.planetminecraft.com/server/cobblemon-conquest/vote/"},
    {"name": "Minecraft Servers", "url": "https://minecraft-servers.com/vote/cobblemon-conquest/"},
]

# Discord webhook URLs – set real values in settings_private.py
DISCORD_APPEALS_WEBHOOK = ""
DISCORD_STAFFAPPS_WEBHOOK = ""

# ── Discord bot / Roadmap management ─────────────────────────────────────
# Bot token for the roadmap-management Discord bot (set in settings_private.py)
DISCORD_BOT_TOKEN = ""
# Discord role names (case-insensitive) that may use roadmap bot commands
DISCORD_ROADMAP_ALLOWED_ROLES = ["Admin", "Owner", "Staff Manager"]
# Secret key used by the Discord bot to authenticate against the site API
# Generate with: python -c "import secrets; print(secrets.token_hex(32))"
ROADMAP_API_SECRET = ""

# ── Staff portal auth ───────────────────────────────────────────────────────
DISCORD_OAUTH_CLIENT_ID = ""
DISCORD_OAUTH_CLIENT_SECRET = ""
DISCORD_OAUTH_REDIRECT_URI = ""
DISCORD_STAFF_GUILD_ID = ""
# Role ID to access level mapping (viewer/editor/admin)
DISCORD_STAFF_ROLE_LEVELS = {}
# Optional role name to access level mapping (case-insensitive)
DISCORD_STAFF_ROLE_LEVELS_BY_NAME = {}

# Staff roles available for applications (ordered by seniority ascending)
STAFF_ROLES = ["Helper"]

try:
    from .settings_private import *  # noqa: F401,F403
except (ImportError, ModuleNotFoundError):
    pass
