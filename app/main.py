"""HTTP routes: the public widget/chat API and the password-protected admin API."""

import logging
import re
import secrets
import sqlite3
from contextlib import asynccontextmanager, closing
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from app import config, llm, security
from app.db import connect, get_db, init_db
from app.security import limiter, require_admin
from app.seed import seed_if_empty

logging.basicConfig(level=logging.INFO)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with closing(connect()) as conn, conn:
        seed_if_empty(conn)
    yield


app = FastAPI(title="AI Support Widget", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
# scope="function" commits before the response goes out, so a client never sees
# success for a write that then fails to commit.
Db = Depends(get_db, scope="function")


# --- Helpers ------------------------------------------------------------------

def get_bot(db: sqlite3.Connection, bot_id: str) -> dict:
    row = db.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Bot not found")
    return dict(row)


def get_faqs(db: sqlite3.Connection, bot_id: str) -> list[dict]:
    rows = db.execute("SELECT id, question, answer FROM faqs WHERE bot_id = ? ORDER BY id", (bot_id,))
    return [dict(r) for r in rows]


def messages_this_month(db: sqlite3.Connection, bot_id: str) -> int:
    return db.execute(
        """SELECT COUNT(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id
           WHERE c.bot_id = ? AND m.role = 'assistant' AND m.created_at >= date('now', 'start of month')""",
        (bot_id,),
    ).fetchone()[0]


def load_history(db: sqlite3.Connection, conversation_id: str) -> list[dict]:
    rows = db.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
        (conversation_id, config.HISTORY_LIMIT),
    ).fetchall()
    history = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    # The model expects the conversation to open with a customer message.
    while history and history[0]["role"] != "user":
        history.pop(0)
    return history


# --- Public: widget, chat page and chat API -------------------------------------

@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse("/admin")


@app.get("/widget.js", include_in_schema=False)
def widget_js():
    return FileResponse(STATIC_DIR / "widget.js", media_type="text/javascript",
                        headers={"Cache-Control": "public, max-age=300"})


@app.get("/chat", response_class=HTMLResponse, include_in_schema=False)
def chat_page(bot: str, db: sqlite3.Connection = Db):
    row = db.execute("SELECT allowed_origins FROM bots WHERE id = ?", (bot,)).fetchone()
    if row is None:
        raise HTTPException(404, "Bot not found")
    # Browsers refuse to show this page inside an iframe on any site not on the bot's list,
    # so copying the script tag onto another website doesn't work.
    origins = security.parse_origins(row["allowed_origins"])
    return HTMLResponse(
        (STATIC_DIR / "chat.html").read_text(),
        headers={"Content-Security-Policy": security.frame_ancestors_policy(origins)},
    )


@app.get("/api/bots/{bot_id}/config")
def public_bot_config(bot_id: str, db: sqlite3.Connection = Db):
    bot = get_bot(db, bot_id)
    return {"name": bot["name"], "greeting": bot["greeting"], "accent_color": bot["accent_color"]}


class ChatIn(BaseModel):
    bot_id: str = Field(max_length=64)
    conversation_id: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=2000)


@app.post("/api/chat")
def chat(body: ChatIn, request: Request, db: sqlite3.Connection = Db):
    bot = get_bot(db, body.bot_id)
    message = body.message.strip()
    if not message:
        raise HTTPException(422, "Message is empty")

    if not limiter.allow(f"visitor:{security.client_ip(request)}:{bot['id']}", config.VISITOR_MESSAGES_PER_MINUTE):
        raise HTTPException(429, "You're sending messages too quickly. Please wait a moment.")
    if not limiter.allow(f"bot:{bot['id']}", config.BOT_MESSAGES_PER_MINUTE):
        raise HTTPException(429, "We're very busy right now. Please try again in a minute.")
    if messages_this_month(db, bot["id"]) >= bot["monthly_message_cap"]:
        raise HTTPException(429, "Chat is unavailable right now. Please contact us another way.")

    # The server keeps the transcript. The browser only holds an unguessable ID, so a
    # visitor can't put words in the assistant's mouth by editing earlier messages.
    conversation_id = body.conversation_id
    known = conversation_id and db.execute(
        "SELECT 1 FROM conversations WHERE id = ? AND bot_id = ?", (conversation_id, bot["id"])
    ).fetchone()
    history = load_history(db, conversation_id) if known else []
    history.append({"role": "user", "content": message})

    try:
        reply = llm.generate_reply(bot, get_faqs(db, bot["id"]), history)
    except llm.LLMUnavailable:
        raise HTTPException(503, "Sorry, I'm having trouble right now. Please try again in a moment.")

    if not known:
        conversation_id = secrets.token_urlsafe(18)
        db.execute("INSERT INTO conversations (id, bot_id) VALUES (?, ?)", (conversation_id, bot["id"]))
    db.execute("INSERT INTO messages (conversation_id, role, content) VALUES (?, 'user', ?)",
               (conversation_id, message))
    db.execute("INSERT INTO messages (conversation_id, role, content, answered) VALUES (?, 'assistant', ?, ?)",
               (conversation_id, reply.text, int(reply.answered)))
    db.execute("UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (conversation_id,))
    return {"conversation_id": conversation_id, "reply": reply.text, "needs_human": reply.needs_human}


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class HandoffIn(BaseModel):
    bot_id: str = Field(max_length=64)
    conversation_id: str | None = Field(default=None, max_length=64)
    email: str = Field(max_length=254)
    note: str = Field(default="", max_length=2000)

    @field_validator("email")
    @classmethod
    def check_email(cls, value: str) -> str:
        value = value.strip()
        if not EMAIL_RE.match(value):
            raise ValueError("Please enter a valid email address")
        return value


@app.post("/api/handoff", status_code=201)
def request_human(body: HandoffIn, request: Request, db: sqlite3.Connection = Db):
    bot = get_bot(db, body.bot_id)
    if not limiter.allow(f"handoff:{security.client_ip(request)}", 3):
        raise HTTPException(429, "Please wait a moment before trying again.")
    conversation_id = body.conversation_id
    if conversation_id and not db.execute(
        "SELECT 1 FROM conversations WHERE id = ? AND bot_id = ?", (conversation_id, bot["id"])
    ).fetchone():
        conversation_id = None
    db.execute("INSERT INTO handoffs (bot_id, conversation_id, email, note) VALUES (?, ?, ?, ?)",
               (bot["id"], conversation_id, body.email, body.note.strip()))
    return {"ok": True}


# --- Admin ----------------------------------------------------------------------

@app.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(STATIC_DIR / "admin.html")


class LoginIn(BaseModel):
    password: str = Field(max_length=200)


@app.post("/admin/api/login")
def login(body: LoginIn, request: Request, response: Response):
    if not config.ADMIN_PASSWORD:
        raise HTTPException(503, "Set ADMIN_PASSWORD in .env to enable the dashboard")
    if not limiter.allow(f"login:{security.client_ip(request)}", config.LOGIN_ATTEMPTS_PER_MINUTE):
        raise HTTPException(429, "Too many attempts. Wait a minute and try again.")
    if not security.password_matches(body.password):
        raise HTTPException(401, "Wrong password")
    response.set_cookie(security.SESSION_COOKIE, security.make_session_token(), max_age=security.SESSION_SECONDS,
                        httponly=True, samesite="strict", secure=config.SECURE_COOKIES, path="/admin")
    return {"ok": True}


@app.post("/admin/api/logout")
def logout(response: Response):
    response.delete_cookie(security.SESSION_COOKIE, path="/admin")
    return {"ok": True}


admin = Depends(require_admin)


@app.get("/admin/api/me", dependencies=[admin])
def me():
    return {"llm_mode": config.LLM_MODE, "model": config.CLAUDE_MODEL}


class BotSettings(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    greeting: str = Field(min_length=1, max_length=300)
    accent_color: str = "#4f46e5"
    allowed_origins: str = Field(default="", max_length=2000)
    extra_info: str = Field(default="", max_length=50_000)
    monthly_message_cap: int = Field(default=1000, ge=0, le=1_000_000)

    @field_validator("accent_color")
    @classmethod
    def check_color(cls, value: str) -> str:
        if not security.is_valid_color(value):
            raise ValueError("Colour must look like #1f7a5a")
        return value

    @field_validator("allowed_origins")
    @classmethod
    def check_origins(cls, value: str) -> str:
        return "\n".join(security.parse_origins(value))


class NewBot(BotSettings):
    id: str

    @field_validator("id")
    @classmethod
    def check_id(cls, value: str) -> str:
        if not security.is_valid_bot_id(value):
            raise ValueError("Use 2-63 lowercase letters, numbers and dashes")
        return value


@app.get("/admin/api/bots", dependencies=[admin])
def list_bots(db: sqlite3.Connection = Db):
    return [dict(r) for r in db.execute("SELECT id, name FROM bots ORDER BY created_at, rowid")]


@app.post("/admin/api/bots", dependencies=[admin], status_code=201)
def create_bot(body: NewBot, db: sqlite3.Connection = Db):
    if db.execute("SELECT 1 FROM bots WHERE id = ?", (body.id,)).fetchone():
        raise HTTPException(409, "A bot with that ID already exists")
    db.execute(
        """INSERT INTO bots (id, name, greeting, accent_color, allowed_origins, extra_info, monthly_message_cap)
           VALUES (:id, :name, :greeting, :accent_color, :allowed_origins, :extra_info, :monthly_message_cap)""",
        body.model_dump(),
    )
    return get_bot(db, body.id)


@app.get("/admin/api/bots/{bot_id}", dependencies=[admin])
def read_bot(bot_id: str, db: sqlite3.Connection = Db):
    return get_bot(db, bot_id)


@app.put("/admin/api/bots/{bot_id}", dependencies=[admin])
def update_bot(bot_id: str, body: BotSettings, db: sqlite3.Connection = Db):
    get_bot(db, bot_id)
    db.execute(
        """UPDATE bots SET name = :name, greeting = :greeting, accent_color = :accent_color,
           allowed_origins = :allowed_origins, extra_info = :extra_info,
           monthly_message_cap = :monthly_message_cap WHERE id = :id""",
        {**body.model_dump(), "id": bot_id},
    )
    return get_bot(db, bot_id)


@app.delete("/admin/api/bots/{bot_id}", dependencies=[admin], status_code=204)
def delete_bot(bot_id: str, db: sqlite3.Connection = Db):
    get_bot(db, bot_id)
    db.execute("DELETE FROM bots WHERE id = ?", (bot_id,))


@app.get("/admin/api/bots/{bot_id}/stats", dependencies=[admin])
def bot_stats(bot_id: str, db: sqlite3.Connection = Db):
    bot = get_bot(db, bot_id)

    def count(sql: str) -> int:
        return db.execute(sql, (bot_id,)).fetchone()[0]

    return {
        "messages_this_month": messages_this_month(db, bot_id),
        "monthly_message_cap": bot["monthly_message_cap"],
        "conversations": count("SELECT COUNT(*) FROM conversations WHERE bot_id = ?"),
        "unanswered": count("""SELECT COUNT(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id
                               WHERE c.bot_id = ? AND m.role = 'assistant' AND m.answered = 0"""),
        "open_handoffs": count("SELECT COUNT(*) FROM handoffs WHERE bot_id = ? AND resolved = 0"),
        "faqs": count("SELECT COUNT(*) FROM faqs WHERE bot_id = ?"),
    }


class FaqIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=3000)


@app.get("/admin/api/bots/{bot_id}/faqs", dependencies=[admin])
def list_faqs(bot_id: str, db: sqlite3.Connection = Db):
    get_bot(db, bot_id)
    return get_faqs(db, bot_id)


@app.post("/admin/api/bots/{bot_id}/faqs", dependencies=[admin], status_code=201)
def add_faq(bot_id: str, body: FaqIn, db: sqlite3.Connection = Db):
    get_bot(db, bot_id)
    cur = db.execute("INSERT INTO faqs (bot_id, question, answer) VALUES (?, ?, ?)",
                     (bot_id, body.question.strip(), body.answer.strip()))
    return {"id": cur.lastrowid, "question": body.question.strip(), "answer": body.answer.strip()}


@app.put("/admin/api/faqs/{faq_id}", dependencies=[admin])
def update_faq(faq_id: int, body: FaqIn, db: sqlite3.Connection = Db):
    cur = db.execute("UPDATE faqs SET question = ?, answer = ? WHERE id = ?",
                     (body.question.strip(), body.answer.strip(), faq_id))
    if cur.rowcount == 0:
        raise HTTPException(404, "FAQ not found")
    return {"id": faq_id, "question": body.question.strip(), "answer": body.answer.strip()}


@app.delete("/admin/api/faqs/{faq_id}", dependencies=[admin], status_code=204)
def delete_faq(faq_id: int, db: sqlite3.Connection = Db):
    if db.execute("DELETE FROM faqs WHERE id = ?", (faq_id,)).rowcount == 0:
        raise HTTPException(404, "FAQ not found")


@app.get("/admin/api/bots/{bot_id}/conversations", dependencies=[admin])
def list_conversations(bot_id: str, db: sqlite3.Connection = Db):
    get_bot(db, bot_id)
    rows = db.execute(
        """SELECT c.id, c.created_at, c.updated_at,
                  (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count,
                  (SELECT content FROM messages m WHERE m.conversation_id = c.id AND m.role = 'user'
                   ORDER BY m.id LIMIT 1) AS first_message,
                  EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id AND m.answered = 0) AS has_unanswered
           FROM conversations c WHERE c.bot_id = ? ORDER BY c.updated_at DESC, c.rowid DESC LIMIT 200""",
        (bot_id,),
    )
    return [dict(r) for r in rows]


@app.get("/admin/api/conversations/{conversation_id}", dependencies=[admin])
def read_conversation(conversation_id: str, db: sqlite3.Connection = Db):
    rows = db.execute(
        "SELECT role, content, answered, created_at FROM messages WHERE conversation_id = ? ORDER BY id",
        (conversation_id,),
    ).fetchall()
    if not rows:
        raise HTTPException(404, "Conversation not found")
    return [dict(r) for r in rows]


@app.get("/admin/api/bots/{bot_id}/unanswered", dependencies=[admin])
def unanswered_questions(bot_id: str, db: sqlite3.Connection = Db):
    get_bot(db, bot_id)
    rows = db.execute(
        """SELECT a.id, a.conversation_id, a.created_at, a.content AS reply,
                  (SELECT u.content FROM messages u WHERE u.conversation_id = a.conversation_id
                   AND u.role = 'user' AND u.id < a.id ORDER BY u.id DESC LIMIT 1) AS question
           FROM messages a JOIN conversations c ON c.id = a.conversation_id
           WHERE c.bot_id = ? AND a.role = 'assistant' AND a.answered = 0
           ORDER BY a.id DESC LIMIT 100""",
        (bot_id,),
    )
    return [dict(r) for r in rows]


@app.get("/admin/api/bots/{bot_id}/handoffs", dependencies=[admin])
def list_handoffs(bot_id: str, db: sqlite3.Connection = Db):
    get_bot(db, bot_id)
    rows = db.execute(
        """SELECT id, conversation_id, email, note, resolved, created_at FROM handoffs
           WHERE bot_id = ? ORDER BY resolved, id DESC LIMIT 200""",
        (bot_id,),
    )
    return [dict(r) for r in rows]


@app.post("/admin/api/handoffs/{handoff_id}/resolve", dependencies=[admin])
def resolve_handoff(handoff_id: int, db: sqlite3.Connection = Db):
    if db.execute("UPDATE handoffs SET resolved = 1 WHERE id = ?", (handoff_id,)).rowcount == 0:
        raise HTTPException(404, "Request not found")
    return {"ok": True}
