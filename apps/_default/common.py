"""
Common fixtures shared across controllers.
"""

from py4web import DAL, Field, Session, Translator

from . import settings

# Database
db = DAL(
    settings.DB_URI,
    folder=settings.DB_FOLDER,
    pool_size=settings.DB_POOL_SIZE,
    migrate=settings.DB_MIGRATE,
    fake_migrate=settings.DB_FAKE_MIGRATE,
)

# Internationalisation (English-only; Translator still required as a fixture)
T = Translator(settings.T_FOLDER)

# Session (signed cookies – no extra dependencies)
session = Session(secret=settings.SESSION_SECRET_KEY or "conquest-default-secret-change-me")
