// Chat UI running inside the iframe that widget.js adds to the host page.
(function () {
  "use strict";

  const params = new URLSearchParams(location.search);
  const botId = params.get("bot");
  const inline = params.get("mode") === "inline"; // admin dashboard preview
  const storageKey = `support-chat:${botId}${inline ? ":preview" : ""}`;

  const $ = (id) => document.getElementById(id);
  const launcher = $("launcher");
  const panel = $("panel");
  const messages = $("messages");
  const composer = $("composer");
  const input = $("input");
  const sendButton = $("send");
  const handoff = $("handoff");

  let botConfig = { name: "Support", greeting: "Hi! How can I help you today?" };
  let state = loadState() || { conversationId: null, history: [], open: false, handedOff: false };
  let sending = false;

  // sessionStorage keeps the chat open and its messages while the visitor moves between
  // pages of the host site. It can be unavailable (privacy modes), so every access is guarded.
  function loadState() {
    try {
      return JSON.parse(sessionStorage.getItem(storageKey));
    } catch {
      return null;
    }
  }
  function saveState() {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(state));
    } catch {
      /* chat still works, it just won't survive a page change */
    }
  }

  // focus is false when restoring an open chat on page load, so we don't steal focus from the host page.
  function setOpen(open, { focus = true } = {}) {
    state.open = open;
    saveState();
    panel.hidden = !open;
    launcher.hidden = open;
    launcher.setAttribute("aria-expanded", String(open));
    if (!inline) parent.postMessage({ type: "support-widget:resize", open }, "*");
    if (open) scrollToBottom();
    if (focus) (open ? input : launcher).focus();
  }

  function scrollToBottom() {
    messages.scrollTop = messages.scrollHeight;
  }

  // Always textContent, never innerHTML: replies and visitor text are untrusted.
  function bubble(kind, text) {
    const div = document.createElement("div");
    div.className = `msg ${kind}`;
    div.textContent = text;
    messages.append(div);
    scrollToBottom();
    return div;
  }

  function addMessage(role, text) {
    bubble(role, text);
    state.history.push({ role, text });
    saveState();
  }

  function showTyping() {
    const div = bubble("assistant", "");
    div.setAttribute("aria-label", "Assistant is typing");
    const dots = document.createElement("span");
    dots.className = "typing";
    dots.append(document.createElement("span"), document.createElement("span"), document.createElement("span"));
    div.append(dots);
    return div;
  }

  function offerHuman() {
    if (state.handedOff || !handoff.hidden || messages.querySelector(".suggest")) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "suggest";
    button.textContent = "Talk to a person";
    button.addEventListener("click", () => {
      button.remove();
      openHandoff();
    });
    messages.append(button);
    scrollToBottom();
  }

  function openHandoff() {
    if (state.handedOff) {
      bubble("notice", "You've already sent your details. The team will email you soon.");
      return;
    }
    messages.querySelector(".suggest")?.remove();
    handoff.hidden = false;
    composer.hidden = true;
    $("handoff-email").focus();
  }

  function closeHandoff() {
    handoff.hidden = true;
    composer.hidden = false;
    input.focus();
  }

  async function postJSON(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof data.detail === "string" ? data.detail : "Something went wrong. Please try again.";
      throw new Error(detail);
    }
    return data;
  }

  async function send(text) {
    if (sending) return;
    sending = true;
    sendButton.disabled = true;
    messages.querySelector(".suggest")?.remove();
    addMessage("user", text);
    const typing = showTyping();
    try {
      const data = await postJSON("/api/chat", {
        bot_id: botId,
        conversation_id: state.conversationId,
        message: text,
      });
      state.conversationId = data.conversation_id;
      typing.remove();
      addMessage("assistant", data.reply);
      if (data.needs_human) offerHuman();
    } catch (error) {
      typing.remove();
      bubble("error", error.message);
    } finally {
      sending = false;
      sendButton.disabled = false;
      input.focus();
    }
  }

  function autoGrow() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
  }

  composer.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text || sending) return;
    input.value = "";
    autoGrow();
    send(text);
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      composer.requestSubmit();
    }
  });
  input.addEventListener("input", autoGrow);

  handoff.addEventListener("submit", async (event) => {
    event.preventDefault();
    const email = $("handoff-email").value.trim();
    const submit = handoff.querySelector("button[type=submit]");
    submit.disabled = true;
    try {
      await postJSON("/api/handoff", {
        bot_id: botId,
        conversation_id: state.conversationId,
        email,
        note: $("handoff-note").value,
      });
      state.handedOff = true;
      closeHandoff();
      addMessage("assistant", `Thanks! I've passed this to the team. They'll reply to ${email} as soon as they can.`);
    } catch (error) {
      bubble("error", error.message);
    } finally {
      submit.disabled = false;
    }
  });

  $("handoff-cancel").addEventListener("click", closeHandoff);
  $("human").addEventListener("click", openHandoff);
  launcher.addEventListener("click", () => setOpen(true));
  $("close").addEventListener("click", () => setOpen(false));
  window.addEventListener("message", (event) => {
    if (event.source !== parent || event.data?.type !== "support-widget:layout") return;
    document.body.classList.toggle("fullscreen", Boolean(event.data.fullscreen));
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !panel.hidden && !inline) setOpen(false);
  });

  function render() {
    messages.replaceChildren();
    bubble("assistant", botConfig.greeting);
    for (const message of state.history) bubble(message.role, message.text);
  }

  async function start() {
    if (inline) document.body.classList.add("inline");
    try {
      const response = await fetch(`/api/bots/${encodeURIComponent(botId)}/config`);
      if (response.ok) botConfig = await response.json();
    } catch {
      /* fall back to the defaults above */
    }
    $("bot-name").textContent = botConfig.name;
    if (/^#[0-9a-f]{6}$/i.test(botConfig.accent_color || "")) {
      document.documentElement.style.setProperty("--accent", botConfig.accent_color);
    }
    render();
    if (inline || state.open) setOpen(true, { focus: false });
  }

  start();
})();
