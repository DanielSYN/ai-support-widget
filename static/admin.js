// Business owner dashboard. Everything that came from a website visitor is rendered with
// textContent (via h()), never innerHTML, so a visitor can't inject markup into this page.
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const TABS = ["overview", "knowledge", "conversations", "inbox", "settings"];
  const state = { bots: [], bot: null, tab: "overview", conversationId: null };

  // --- Small helpers ------------------------------------------------------------

  function h(tag, props = {}, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "class") el.className = value;
      else if (key === "value") el.value = value;
      else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
      else el.setAttribute(key, value === true ? "" : value);
    }
    el.append(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
    return el;
  }

  function errorMessage(data) {
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail)) return data.detail.map((d) => d.msg.replace(/^Value error, /, "")).join(" ");
    return "Something went wrong. Please try again.";
  }

  async function api(method, path, body) {
    const response = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (response.status === 401 && !path.endsWith("/login")) {
      showLogin();
      throw new Error("Please sign in again.");
    }
    if (response.status === 204) return null;
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorMessage(data));
    return data;
  }

  let toastTimer;
  function toast(message, isError = false) {
    const el = $("toast");
    el.textContent = message;
    el.className = isError ? "toast error" : "toast";
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (el.hidden = true), 3000);
  }

  // Runs an async action, showing any error as a toast and disabling the button meanwhile.
  async function attempt(button, action) {
    if (button) button.disabled = true;
    try {
      return await action();
    } catch (error) {
      toast(error.message, true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  // SQLite stores UTC as "YYYY-MM-DD HH:MM:SS".
  function formatTime(value) {
    const date = new Date(value.replace(" ", "T") + "Z");
    return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  }

  function remember(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch {
      /* not essential */
    }
  }
  function recall(key) {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  }

  // --- Login --------------------------------------------------------------------

  function showLogin() {
    $("app-view").hidden = true;
    $("login-view").hidden = false;
    $("password").focus();
  }

  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = $("login-error");
    error.hidden = true;
    try {
      await api("POST", "/admin/api/login", { password: $("password").value });
      $("password").value = "";
      await startApp();
    } catch (e) {
      error.textContent = e.message;
      error.hidden = false;
    }
  });

  $("logout").addEventListener("click", async () => {
    await api("POST", "/admin/api/logout").catch(() => {});
    showLogin();
  });

  // --- Bots and tabs -------------------------------------------------------------

  async function loadBots(selectId) {
    state.bots = await api("GET", "/admin/api/bots");
    const select = $("bot-select");
    select.replaceChildren(...state.bots.map((b) => h("option", { value: b.id }, `${b.name} (${b.id})`)));
    if (state.bots.length === 0) {
      state.bot = null;
      $("new-bot-dialog").showModal();
      return;
    }
    const wanted = selectId || recall("admin:bot");
    const id = state.bots.some((b) => b.id === wanted) ? wanted : state.bots[0].id;
    await selectBot(id);
  }

  async function selectBot(id) {
    state.bot = await api("GET", `/admin/api/bots/${encodeURIComponent(id)}`);
    state.conversationId = null;
    $("bot-select").value = id;
    remember("admin:bot", id);
    $("preview").src = `/chat?bot=${encodeURIComponent(id)}&mode=inline`;
    await showTab(state.tab);
  }

  $("bot-select").addEventListener("change", (event) => attempt(null, () => selectBot(event.target.value)));

  async function showTab(tab) {
    if (!TABS.includes(tab)) tab = "overview";
    state.tab = tab;
    for (const name of TABS) {
      $(`tab-${name}`).hidden = name !== tab;
      document.querySelector(`[data-tab="${name}"]`).setAttribute("aria-selected", String(name === tab));
    }
    if (location.hash !== `#${tab}`) history.replaceState(null, "", `#${tab}`);
    if (!state.bot) return;
    const loaders = { overview: loadOverview, knowledge: loadKnowledge, conversations: loadConversations,
                      inbox: loadInbox, settings: loadSettings };
    await attempt(null, loaders[tab]);
    refreshInboxCount();
  }

  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => showTab(button.dataset.tab));
  });
  window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));

  async function refreshInboxCount() {
    if (!state.bot) return;
    const stats = await api("GET", `/admin/api/bots/${state.bot.id}/stats`).catch(() => null);
    const badge = $("inbox-count");
    badge.hidden = !stats || stats.open_handoffs === 0;
    if (stats) badge.textContent = stats.open_handoffs;
  }

  // --- Overview -----------------------------------------------------------------

  function stat(label, value, sub, meterFraction) {
    return h("div", { class: "stat" },
      h("div", { class: "stat-label" }, label),
      h("div", { class: "stat-value" }, String(value)),
      sub ? h("div", { class: "stat-sub" }, sub) : null,
      meterFraction !== undefined
        ? h("div", { class: "meter" }, h("span", { style: `width:${Math.min(100, meterFraction * 100).toFixed(1)}%` }))
        : null,
    );
  }

  async function loadOverview() {
    const s = await api("GET", `/admin/api/bots/${state.bot.id}/stats`);
    const cap = s.monthly_message_cap;
    $("stats").replaceChildren(
      stat("Replies this month", s.messages_this_month, `of ${cap.toLocaleString()} limit`,
           cap ? s.messages_this_month / cap : 1),
      stat("Conversations", s.conversations, "all time"),
      stat("Unanswered questions", s.unanswered, "add FAQs to fix these"),
      stat("Waiting for a person", s.open_handoffs, "see Inbox"),
      stat("FAQs", s.faqs, "in the knowledge base"),
    );
    $("snippet").textContent =
      `<script src="${location.origin}/widget.js" data-bot-id="${state.bot.id}" defer></script>`;
  }

  $("copy-snippet").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("snippet").textContent);
      toast("Copied to clipboard");
    } catch {
      toast("Couldn't copy. Select the code and copy it manually.", true);
    }
  });

  $("reset-preview").addEventListener("click", () => {
    try {
      sessionStorage.removeItem(`support-chat:${state.bot.id}:preview`);
    } catch {
      /* nothing stored */
    }
    $("preview").src = $("preview").src;
  });

  // --- Knowledge ----------------------------------------------------------------

  function faqItem(faq) {
    const question = h("input", { value: faq.question, maxlength: 500, "aria-label": "Question" });
    const answer = h("textarea", { rows: 2, maxlength: 3000, "aria-label": "Answer", value: faq.answer });
    const save = h("button", { class: "btn small", type: "button" }, "Save");
    const remove = h("button", { class: "btn ghost small", type: "button" }, "Delete");
    save.addEventListener("click", () => attempt(save, async () => {
      await api("PUT", `/admin/api/faqs/${faq.id}`, { question: question.value, answer: answer.value });
      toast("FAQ saved");
    }));
    remove.addEventListener("click", () => attempt(remove, async () => {
      if (!confirm("Delete this FAQ?")) return;
      await api("DELETE", `/admin/api/faqs/${faq.id}`);
      item.remove();
      toast("FAQ deleted");
    }));
    const item = h("div", { class: "faq-item" }, question, answer, h("div", { class: "row" }, save, remove));
    return item;
  }

  function updateExtraCount() {
    const length = $("extra-info").value.length;
    $("extra-count").textContent = `${length.toLocaleString()} / 50,000 characters`;
  }

  async function loadKnowledge() {
    const faqs = await api("GET", `/admin/api/bots/${state.bot.id}/faqs`);
    $("faq-list").replaceChildren(
      ...(faqs.length ? faqs.map(faqItem) : [h("p", { class: "empty" }, "No FAQs yet. Add your first one above.")]),
    );
    $("extra-info").value = state.bot.extra_info;
    updateExtraCount();
  }

  $("faq-add").addEventListener("submit", (event) => {
    event.preventDefault();
    const button = event.submitter;
    attempt(button, async () => {
      await api("POST", `/admin/api/bots/${state.bot.id}/faqs`, {
        question: $("faq-question").value,
        answer: $("faq-answer").value,
      });
      $("faq-question").value = "";
      $("faq-answer").value = "";
      toast("FAQ added");
      await loadKnowledge();
    });
  });

  $("extra-info").addEventListener("input", updateExtraCount);

  // Files are read in the browser and appended to the text box; the server only ever sees text.
  $("import-file").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    event.target.value = "";
    if (!file) return;
    if (file.size > 200_000) return toast("That file is too large. Keep it under 200 KB.", true);
    const text = await file.text();
    const box = $("extra-info");
    box.value = box.value.trim() ? `${box.value.trim()}\n\n${text.trim()}` : text.trim();
    updateExtraCount();
    toast(`Imported ${file.name}. Click Save to keep it.`);
  });

  function settingsPayload(overrides = {}) {
    const b = state.bot;
    return {
      name: b.name,
      greeting: b.greeting,
      accent_color: b.accent_color,
      allowed_origins: b.allowed_origins,
      extra_info: b.extra_info,
      monthly_message_cap: b.monthly_message_cap,
      ...overrides,
    };
  }

  $("save-extra").addEventListener("click", (event) => attempt(event.currentTarget, async () => {
    state.bot = await api("PUT", `/admin/api/bots/${state.bot.id}`,
                          settingsPayload({ extra_info: $("extra-info").value }));
    toast("Information saved");
  }));

  // --- Conversations ------------------------------------------------------------

  async function loadConversations() {
    const conversations = await api("GET", `/admin/api/bots/${state.bot.id}/conversations`);
    const list = $("conversation-list");
    if (!conversations.length) {
      list.replaceChildren(h("p", { class: "empty" }, "No conversations yet. Try the chat on the Overview tab."));
      $("transcript").replaceChildren(h("p", { class: "muted" }, "Select a conversation."));
      return;
    }
    list.replaceChildren(...conversations.map((c) =>
      h("button", {
        class: `conversation${c.id === state.conversationId ? " active" : ""}`,
        type: "button",
        "data-id": c.id,
        onclick: () => openConversation(c.id),
      },
        h("div", { class: "conversation-title" }, c.first_message || "(no messages)"),
        h("div", { class: "conversation-meta" },
          `${formatTime(c.updated_at)} · ${c.message_count} messages`,
          c.has_unanswered ? h("span", { class: "flag" }, "unanswered") : null),
      ),
    ));
    await openConversation(state.conversationId || conversations[0].id);
  }

  async function openConversation(id) {
    state.conversationId = id;
    document.querySelectorAll(".conversation").forEach((el) => el.classList.toggle("active", el.dataset.id === id));
    const messages = await api("GET", `/admin/api/conversations/${encodeURIComponent(id)}`);
    $("transcript").replaceChildren(...messages.map((m) =>
      h("div", { class: `bubble ${m.role}${m.answered === 0 ? " unanswered" : ""}` },
        m.content,
        h("div", { class: "bubble-meta" },
          formatTime(m.created_at) + (m.answered === 0 ? " · not in knowledge base" : "")),
      ),
    ));
  }

  // --- Inbox --------------------------------------------------------------------

  function viewConversationButton(conversationId) {
    if (!conversationId) return null;
    return h("button", {
      class: "btn small",
      type: "button",
      onclick: () => {
        state.conversationId = conversationId;
        showTab("conversations");
      },
    }, "View chat");
  }

  async function loadInbox() {
    const [handoffs, unanswered] = await Promise.all([
      api("GET", `/admin/api/bots/${state.bot.id}/handoffs`),
      api("GET", `/admin/api/bots/${state.bot.id}/unanswered`),
    ]);

    $("handoff-list").replaceChildren(...(handoffs.length ? handoffs.map((item) => {
      const resolve = item.resolved ? null : h("button", { class: "btn small primary", type: "button" }, "Mark done");
      resolve?.addEventListener("click", () => attempt(resolve, async () => {
        await api("POST", `/admin/api/handoffs/${item.id}/resolve`);
        await loadInbox();
        refreshInboxCount();
      }));
      return h("div", { class: `inbox-item${item.resolved ? " resolved" : ""}` },
        h("div", { class: "inbox-main" },
          h("div", { class: "inbox-title" }, h("a", { href: `mailto:${item.email}` }, item.email)),
          item.note ? h("p", { class: "inbox-text" }, item.note) : null,
          h("div", { class: "inbox-meta" }, formatTime(item.created_at) + (item.resolved ? " · done" : ""))),
        h("div", { class: "row" }, viewConversationButton(item.conversation_id), resolve),
      );
    }) : [h("p", { class: "empty" }, "Nobody is waiting. 🎉")]));

    $("unanswered-list").replaceChildren(...(unanswered.length ? unanswered.map((item) =>
      h("div", { class: "inbox-item" },
        h("div", { class: "inbox-main" },
          h("div", { class: "inbox-title" }, item.question || "(question not found)"),
          h("p", { class: "inbox-text muted" }, `Bot replied: ${item.reply}`),
          h("div", { class: "inbox-meta" }, formatTime(item.created_at))),
        h("div", { class: "row" },
          viewConversationButton(item.conversation_id),
          h("button", {
            class: "btn small primary",
            type: "button",
            onclick: async () => {
              await showTab("knowledge");
              $("faq-question").value = item.question || "";
              $("faq-answer").focus();
              $("faq-add").scrollIntoView({ behavior: "smooth", block: "center" });
            },
          }, "Add as FAQ")),
      ),
    ) : [h("p", { class: "empty" }, "The bot has answered everything so far.")]));
  }

  // --- Settings -----------------------------------------------------------------

  function loadSettings() {
    const b = state.bot;
    $("s-name").value = b.name;
    $("s-greeting").value = b.greeting;
    $("s-color").value = b.accent_color;
    $("s-origins").value = b.allowed_origins;
    $("s-cap").value = b.monthly_message_cap;
  }

  $("settings-form").addEventListener("submit", (event) => {
    event.preventDefault();
    attempt(event.submitter, async () => {
      state.bot = await api("PUT", `/admin/api/bots/${state.bot.id}`, settingsPayload({
        name: $("s-name").value,
        greeting: $("s-greeting").value,
        accent_color: $("s-color").value,
        allowed_origins: $("s-origins").value,
        monthly_message_cap: Number($("s-cap").value),
      }));
      loadSettings();
      toast("Settings saved");
      $("preview").src = $("preview").src;
      await loadBots(state.bot.id);
    });
  });

  $("delete-bot").addEventListener("click", (event) => attempt(event.currentTarget, async () => {
    const answer = prompt(`This permanently deletes "${state.bot.name}" and all its data.\nType the bot ID (${state.bot.id}) to confirm:`);
    if (answer !== state.bot.id) return;
    await api("DELETE", `/admin/api/bots/${state.bot.id}`);
    toast("Bot deleted");
    state.tab = "overview";
    await loadBots();
  }));

  // --- New bot --------------------------------------------------------------------

  const dialog = $("new-bot-dialog");
  $("new-bot").addEventListener("click", () => {
    $("new-bot-error").hidden = true;
    dialog.showModal();
  });
  $("new-bot-cancel").addEventListener("click", () => dialog.close());

  // Suggest an ID from the name until the owner edits the ID themselves.
  let idEdited = false;
  $("nb-id").addEventListener("input", () => (idEdited = true));
  $("nb-name").addEventListener("input", (event) => {
    if (idEdited) return;
    $("nb-id").value = event.target.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 63);
  });

  $("new-bot-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = $("new-bot-error");
    error.hidden = true;
    try {
      const bot = await api("POST", "/admin/api/bots", {
        id: $("nb-id").value,
        name: $("nb-name").value,
        greeting: "Hi! How can I help you today?",
      });
      dialog.close();
      event.target.reset();
      idEdited = false;
      state.tab = "settings";
      await loadBots(bot.id);
      toast("Bot created. Add the websites it will run on, then your FAQs.");
    } catch (e) {
      error.textContent = e.message;
      error.hidden = false;
    }
  });

  // --- Start --------------------------------------------------------------------

  async function startApp() {
    const me = await api("GET", "/admin/api/me");
    const badge = $("mode-badge");
    if (me.llm_mode === "mock") {
      badge.textContent = "Offline demo mode: set ANTHROPIC_API_KEY for real AI";
      badge.className = "badge warn";
    } else {
      badge.textContent = `AI: ${me.model}`;
      badge.className = "badge";
    }
    $("login-view").hidden = true;
    $("app-view").hidden = false;
    state.tab = TABS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "overview";
    await loadBots();
  }

  startApp().catch(() => showLogin());
})();
