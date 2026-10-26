/*
 * Support chat embed script. Businesses add one tag to their site:
 *
 *   <script src="https://your-server.com/widget.js" data-bot-id="your-bot" defer></script>
 *
 * The chat itself runs inside an iframe served from our domain, so the host page's CSS
 * and JavaScript can't break it, and it can't break the host page.
 */
(function () {
  if (window.__supportWidgetLoaded) return;
  window.__supportWidgetLoaded = true;

  var script = document.currentScript;
  var botId = script && script.getAttribute("data-bot-id");
  if (!botId) {
    console.error("[support-widget] Missing data-bot-id on the script tag.");
    return;
  }
  var origin = new URL(script.src).origin;

  var CLOSED = { width: "76px", height: "76px", right: "12px", bottom: "12px" };
  var OPEN = {
    width: "min(408px, calc(100vw - 16px))",
    height: "min(660px, calc(100vh - 16px))",
    right: "8px",
    bottom: "8px",
  };
  var FULLSCREEN = { width: "100vw", height: "100dvh", right: "0", bottom: "0" };

  function mount() {
    var iframe = document.createElement("iframe");
    iframe.src = origin + "/chat?bot=" + encodeURIComponent(botId);
    iframe.title = "Customer support chat";
    iframe.setAttribute("allowtransparency", "true");
    var style = iframe.style;
    style.position = "fixed";
    style.zIndex = "2147483647";
    style.border = "0";
    style.background = "transparent";
    style.colorScheme = "light";
    style.maxWidth = "100vw";
    style.maxHeight = "100vh";
    Object.assign(style, CLOSED);
    document.body.appendChild(iframe);

    window.addEventListener("message", function (event) {
      // Only listen to our own chat frame, never to other scripts on the page.
      if (event.origin !== origin || event.source !== iframe.contentWindow) return;
      var data = event.data || {};
      if (data.type !== "support-widget:resize") return;
      var fullscreen = data.open && window.innerWidth < 480;
      Object.assign(style, !data.open ? CLOSED : fullscreen ? FULLSCREEN : OPEN);
      // Only this page knows the screen width; tell the chat so it can drop its card styling.
      iframe.contentWindow.postMessage({ type: "support-widget:layout", fullscreen: fullscreen }, origin);
    });
  }

  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount);
})();
