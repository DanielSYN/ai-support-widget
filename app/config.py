"""Settings, read from environment variables (or a .env file in the project root)."""

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", BASE_DIR / "data" / "support.db"))

# "claude" calls the real model; "mock" answers from FAQs by keyword matching so the
# whole product can be tried (and tested) without an API key.
LLM_MODE = os.environ.get("LLM_MODE") or ("claude" if os.environ.get("ANTHROPIC_API_KEY") else "mock")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
# Signs admin session cookies. Without one, a random key is used and admins are
# logged out whenever the server restarts.
SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
# Set to 1 when serving over HTTPS so the admin cookie is only sent over TLS.
SECURE_COOKIES = os.environ.get("SECURE_COOKIES") == "1"

# Per visitor IP, per bot. Stops one person hammering a bot.
VISITOR_MESSAGES_PER_MINUTE = int(os.environ.get("VISITOR_MESSAGES_PER_MINUTE", "8"))
# Per bot across all visitors. Caps the damage if someone scripts against a bot ID.
BOT_MESSAGES_PER_MINUTE = int(os.environ.get("BOT_MESSAGES_PER_MINUTE", "60"))
LOGIN_ATTEMPTS_PER_MINUTE = 5

# How many earlier messages of a conversation are sent to the model.
HISTORY_LIMIT = 20
