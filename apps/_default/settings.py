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
DISCORD_INVITE_URL = "https://discord.gg/cobblemonconquest"

VOTE_SITES = [
    {"name": "Minecraft Server List", "url": "https://minecraft-server-list.com/server/cobblemon-conquest/vote/"},
    {"name": "Planet Minecraft", "url": "https://www.planetminecraft.com/server/cobblemon-conquest/vote/"},
    {"name": "Minecraft Servers", "url": "https://minecraft-servers.com/vote/cobblemon-conquest/"},
]

# Discord webhook URLs – set real values in settings_private.py
DISCORD_APPEALS_WEBHOOK = ""
DISCORD_STAFFAPPS_WEBHOOK = ""

# Staff roles available for applications (ordered by seniority ascending)
STAFF_ROLES = ["Helper"]

try:
    from .settings_private import *  # noqa: F401,F403
except (ImportError, ModuleNotFoundError):
    pass
