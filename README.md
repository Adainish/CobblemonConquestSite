# CobblemonConquestSite

A full-featured website for **Cobblemon Conquest** – a feudal Japan themed Cobblemon Minecraft server on Fabric.

Built with **py4web** (Python), designed for up to 150 concurrent visitors, and optimised for Google indexing.

---

## Features

| Feature | Details |
|---|---|
| 🏯 Themed design | Feudal Japan + Cobblemon colour palette, responsive layout |
| 📦 Modpack page | CurseForge install guide |
| 🗳️ Voting page | Links to vote sites with reward info |
| 📋 Roadmap | DB-backed roadmap manageable by staff |
| ⚖️ Ban / Mute Appeals | Form → spam check → Discord webhook + Discord bot voting |
| 🎖️ Staff Applications | Helper (+ scalable roles) → Discord webhook + Discord bot voting |
| 🔍 SEO | Schema.org, Open Graph, Twitter Card, meta descriptions |

---

## Quick Start

```bash
# 1. Clone & enter repo
git clone https://github.com/Adainish/CobblemonConquestSite.git
cd CobblemonConquestSite

# 2. Create virtual environment
python3 -m venv .venv && source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure private settings
cp apps/conquest/settings_private.py.example apps/conquest/settings_private.py
# Edit settings_private.py with your Discord webhook URLs and a secret key

# 5. Run (development)
py4web run apps --host 127.0.0.1 --port 8000
# Visit http://127.0.0.1:8000/conquest
```

For production behind Nginx/Caddy, run `start.py` which binds to `0.0.0.0:8000`.

---

## Configuration (`apps/conquest/settings_private.py`)

| Variable | Description |
|---|---|
| `SESSION_SECRET_KEY` | Long random string for cookie signing |
| `DISCORD_APPEALS_WEBHOOK` | Discord webhook URL for appeal notifications |
| `DISCORD_STAFFAPPS_WEBHOOK` | Discord webhook URL for staff application notifications |
| `SERVER_IP` | Minecraft server address (override if needed) |
| `STORE_URL` | Store URL (override if needed) |

Optional (for reaction voting on appeal embeds):

| Variable | Description |
|---|---|
| `DISCORD_BOT_TOKEN` | Discord bot token (for adding ✅/❌ reactions) |
| `DISCORD_APPEALS_CHANNEL_ID` | Channel ID where appeals are posted |

---

## Managing the Roadmap

Roadmap items live in the `roadmap_item` table (SQLite). You can manage them via:

```python
# In the py4web dashboard or a quick script:
from apps.conquest.common import db
db.roadmap_item.insert(title="Season 2 launch", description="New map, new quests!", status="planned", sort_order=10)
db.commit()
```

Statuses: `planned` | `in_progress` | `completed` | `cancelled`

---

## Running Tests

```bash
python -m pytest tests/ -v
```

---

## Project Structure

```
apps/
  conquest/
    controllers.py       # URL routes and business logic
    models.py            # Database tables
    common.py            # Shared fixtures (db, session, T)
    settings.py          # App configuration
    settings_private.py  # Secret values (git-ignored)
    templates/           # HTML templates (yatl)
    static/
      css/style.css      # Main stylesheet
      js/main.js         # Client-side JS
tests/
  test_conquest.py       # pytest test suite
requirements.txt
start.py                 # Production startup helper
```
