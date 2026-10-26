from app.seed import DEMO_BOT_ID


def test_admin_api_requires_login(client):
    assert client.get("/admin/api/bots").status_code == 401
    assert client.post("/admin/api/login", json={"password": "wrong"}).status_code == 401
    client.cookies.set("admin_session", "9999999999.forged", path="/admin")
    assert client.get("/admin/api/bots").status_code == 401


def test_login_attempts_are_rate_limited(client):
    statuses = [client.post("/admin/api/login", json={"password": "wrong"}).status_code for _ in range(6)]
    assert statuses[-1] == 429


def test_logout_ends_the_session(admin):
    admin.post("/admin/api/logout")
    assert admin.get("/admin/api/bots").status_code == 401


def test_create_bot_validates_input(admin):
    assert admin.post("/admin/api/bots", json={"id": "Bad ID!", "name": "x", "greeting": "hi"}).status_code == 422
    assert admin.post("/admin/api/bots", json={"id": "ok-bot", "name": "x", "greeting": "hi",
                                               "accent_color": "red"}).status_code == 422
    assert admin.post("/admin/api/bots", json={"id": DEMO_BOT_ID, "name": "x", "greeting": "hi"}).status_code == 409

    created = admin.post("/admin/api/bots", json={"id": "acme", "name": "Acme", "greeting": "Hello"})
    assert created.status_code == 201
    assert [b["id"] for b in admin.get("/admin/api/bots").json()] == [DEMO_BOT_ID, "acme"]


def test_allowed_origins_are_normalised_and_injection_is_rejected(admin):
    bot = admin.get(f"/admin/api/bots/{DEMO_BOT_ID}").json()
    saved = admin.put(f"/admin/api/bots/{DEMO_BOT_ID}",
                      json={**bot, "allowed_origins": "https://Shop.Example.com/, https://example.com"})
    assert saved.json()["allowed_origins"] == "https://shop.example.com\nhttps://example.com"

    for bad in ["https://evil.com; script-src *", "*", "example.com", "https://example.com/path"]:
        response = admin.put(f"/admin/api/bots/{DEMO_BOT_ID}", json={**bot, "allowed_origins": bad})
        assert response.status_code == 422, bad


def test_faq_crud(admin):
    created = admin.post(f"/admin/api/bots/{DEMO_BOT_ID}/faqs",
                         json={"question": "Gift wrap?", "answer": "Yes, £3."}).json()
    admin.put(f"/admin/api/faqs/{created['id']}", json={"question": "Gift wrapping?", "answer": "Yes, £3.50."})
    faqs = admin.get(f"/admin/api/bots/{DEMO_BOT_ID}/faqs").json()
    assert faqs[-1] == {"id": created["id"], "question": "Gift wrapping?", "answer": "Yes, £3.50."}

    assert admin.delete(f"/admin/api/faqs/{created['id']}").status_code == 204
    assert admin.delete(f"/admin/api/faqs/{created['id']}").status_code == 404


def test_new_faq_is_used_in_the_next_answer(admin):
    admin.post(f"/admin/api/bots/{DEMO_BOT_ID}/faqs",
               json={"question": "Do you sell kayaks?", "answer": "No, we don't sell kayaks or paddles."})
    reply = admin.post("/api/chat", json={"bot_id": DEMO_BOT_ID, "message": "Do you sell kayaks?"}).json()
    assert "don't sell kayaks" in reply["reply"]


def test_stats_and_conversations(admin):
    conversation_id = admin.post("/api/chat", json={"bot_id": DEMO_BOT_ID, "message": "How do boots fit?"}).json()[
        "conversation_id"]
    stats = admin.get(f"/admin/api/bots/{DEMO_BOT_ID}/stats").json()
    assert stats["messages_this_month"] == 1
    assert stats["conversations"] == 1

    listed = admin.get(f"/admin/api/bots/{DEMO_BOT_ID}/conversations").json()
    assert listed[0]["first_message"] == "How do boots fit?"
    transcript = admin.get(f"/admin/api/conversations/{conversation_id}").json()
    assert [m["role"] for m in transcript] == ["user", "assistant"]


def test_resolve_handoff(admin):
    admin.post("/api/handoff", json={"bot_id": DEMO_BOT_ID, "email": "a@b.co"})
    handoff_id = admin.get(f"/admin/api/bots/{DEMO_BOT_ID}/handoffs").json()[0]["id"]
    admin.post(f"/admin/api/handoffs/{handoff_id}/resolve")
    assert admin.get(f"/admin/api/bots/{DEMO_BOT_ID}/stats").json()["open_handoffs"] == 0


def test_deleting_a_bot_removes_its_data(admin):
    admin.post("/api/chat", json={"bot_id": DEMO_BOT_ID, "message": "hello"})
    assert admin.delete(f"/admin/api/bots/{DEMO_BOT_ID}").status_code == 204
    assert admin.get(f"/admin/api/bots/{DEMO_BOT_ID}").status_code == 404
    assert admin.get("/chat?bot=demo-shop").status_code == 404


def test_parallel_requests_share_the_thread_pool_safely(admin):
    # FastAPI may open a request's DB connection on one worker thread and commit on another.
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda _: admin.get(f"/admin/api/bots/{DEMO_BOT_ID}/stats").status_code, range(40)))
    assert statuses == [200] * 40
