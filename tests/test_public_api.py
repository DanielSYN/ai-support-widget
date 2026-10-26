from app import config
from app.seed import DEMO_BOT_ID


def chat(client, message, conversation_id=None, bot_id=DEMO_BOT_ID):
    return client.post("/api/chat", json={"bot_id": bot_id, "conversation_id": conversation_id, "message": message})


def test_answers_from_faqs_and_keeps_the_conversation(client):
    first = chat(client, "Can I return something after two weeks?")
    assert first.status_code == 200
    body = first.json()
    assert "30 days" in body["reply"]
    assert body["needs_human"] is False

    second = chat(client, "And how long does shipping take?", body["conversation_id"])
    assert second.json()["conversation_id"] == body["conversation_id"]
    assert "3-5 working days" in second.json()["reply"]


def test_server_owns_the_history_sent_to_the_model(client, monkeypatch):
    seen = []

    def fake_reply(bot, faqs, history):
        seen.append(history)
        from app.llm import Reply
        return Reply("ok", True, False)

    monkeypatch.setattr("app.llm.generate_reply", fake_reply)
    conversation_id = chat(client, "first").json()["conversation_id"]
    chat(client, "second", conversation_id)
    assert seen[1] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "second"},
    ]


def test_conversation_from_another_bot_is_not_reused(admin):
    admin.post("/admin/api/bots", json={"id": "other-bot", "name": "Other", "greeting": "Hi"})
    conversation_id = chat(admin, "hello").json()["conversation_id"]
    response = chat(admin, "hello", conversation_id, bot_id="other-bot")
    assert response.status_code == 200
    assert response.json()["conversation_id"] != conversation_id


def test_unknown_question_is_flagged_as_unanswered(admin):
    chat(admin, "Is there a loyalty scheme?")
    unanswered = admin.get(f"/admin/api/bots/{DEMO_BOT_ID}/unanswered").json()
    assert unanswered[0]["question"] == "Is there a loyalty scheme?"


def test_asking_for_a_person_offers_handoff(client):
    assert chat(client, "Can I speak to a real person please").json()["needs_human"] is True


def test_rejects_unknown_bot_and_bad_input(client):
    assert chat(client, "hi", bot_id="nope").status_code == 404
    assert chat(client, "x" * 2001).status_code == 422
    assert chat(client, "   ").status_code == 422


def test_rate_limits_each_visitor(client, monkeypatch):
    monkeypatch.setattr(config, "VISITOR_MESSAGES_PER_MINUTE", 3)
    statuses = [chat(client, "shipping?").status_code for _ in range(4)]
    assert statuses == [200, 200, 200, 429]


def test_monthly_cap_stops_the_bot(admin):
    bot = admin.get(f"/admin/api/bots/{DEMO_BOT_ID}").json()
    admin.put(f"/admin/api/bots/{DEMO_BOT_ID}", json={**bot, "monthly_message_cap": 1})
    assert chat(admin, "shipping?").status_code == 200
    assert chat(admin, "shipping?").status_code == 429


def test_model_outage_returns_503_and_saves_nothing(admin, monkeypatch):
    from app import llm

    def broken(*args):
        raise llm.LLMUnavailable

    monkeypatch.setattr(llm, "generate_reply", broken)
    assert chat(admin, "hello").status_code == 503
    assert admin.get(f"/admin/api/bots/{DEMO_BOT_ID}/conversations").json() == []


def test_chat_page_can_only_be_framed_by_allowed_sites(client):
    response = client.get(f"/chat?bot={DEMO_BOT_ID}")
    assert response.status_code == 200
    assert response.headers["content-security-policy"] == (
        "frame-ancestors 'self' http://localhost:8001 http://127.0.0.1:8001"
    )
    assert client.get("/chat?bot=nope").status_code == 404


def test_public_config_exposes_only_display_fields(client):
    data = client.get(f"/api/bots/{DEMO_BOT_ID}/config").json()
    assert set(data) == {"name", "greeting", "accent_color"}


def test_handoff_reaches_the_inbox(admin):
    conversation_id = chat(admin, "I need help with my order").json()["conversation_id"]
    response = admin.post("/api/handoff", json={
        "bot_id": DEMO_BOT_ID, "conversation_id": conversation_id,
        "email": "sam@example.com", "note": "Order #1234 hasn't arrived",
    })
    assert response.status_code == 201
    inbox = admin.get(f"/admin/api/bots/{DEMO_BOT_ID}/handoffs").json()
    assert inbox[0]["email"] == "sam@example.com"
    assert inbox[0]["conversation_id"] == conversation_id


def test_handoff_rejects_bad_email(client):
    response = client.post("/api/handoff", json={"bot_id": DEMO_BOT_ID, "email": "not-an-email"})
    assert response.status_code == 422
