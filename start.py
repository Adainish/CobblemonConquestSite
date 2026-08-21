#!/usr/bin/env python3
"""
Startup script for Cobblemon Conquest Website.
Runs py4web serving the 'apps' folder on port 8000.

Usage:
    python start.py
    # or for production behind a reverse proxy:
    python start.py --host 127.0.0.1 --port 8000
"""

import sys
from py4web.core import CLI

if __name__ == "__main__":
    sys.argv = [
        "py4web",
        "run",
        "apps",
        "--host", "0.0.0.0",
        "--port", "8000",
        "--password_file", "password.txt",
    ] + sys.argv[1:]
    CLI.run()
