/*! Verity chatbot loader. Embed with:
 *  <script src="https://APP/widget.js" data-verity-key="vb_..." async></script>
 * It adds one iframe; everything else (chat, styles, storage) lives inside it, isolated from your page.
 */
(function () {
  var script = document.currentScript;
  if (!script || window.__verityWidget) return;
  var key = script.getAttribute("data-verity-key");
  if (!key) return;
  window.__verityWidget = true;
  var base = new URL(script.src).origin;
  var frame = document.createElement("iframe");
  frame.title = "Chat";
  frame.setAttribute("allow", "clipboard-write");
  frame.src = base + "/widget/" + encodeURIComponent(key) + "?origin=" + encodeURIComponent(location.origin) + "&page=" + encodeURIComponent(location.href.slice(0, 1500));
  var s = frame.style;
  s.position = "fixed";
  s.bottom = "16px";
  s.right = "16px";
  s.width = "220px";
  s.height = "72px";
  s.border = "0";
  s.background = "transparent";
  s.colorScheme = "normal";
  s.zIndex = "2147483000";
  s.maxWidth = "calc(100vw - 16px)";
  s.maxHeight = "calc(100vh - 16px)";
  frame.setAttribute("data-verity", "");

  function layout(open, position) {
    var small = window.innerWidth < 480;
    s.left = position === "left" ? "16px" : "auto";
    s.right = position === "left" ? "auto" : "16px";
    if (open && small) {
      s.width = "100vw";
      s.height = "100vh";
      s.bottom = "0";
      s.left = s.right = "0";
      s.maxWidth = s.maxHeight = "none";
    } else {
      s.bottom = "16px";
      s.maxWidth = "calc(100vw - 16px)";
      s.maxHeight = "calc(100vh - 16px)";
      s.width = open ? "400px" : "220px";
      s.height = open ? "640px" : "72px";
    }
  }

  window.addEventListener("message", function (e) {
    if (e.origin !== base || e.source !== frame.contentWindow || !e.data || e.data.verity !== true) return;
    if (e.data.type === "layout") layout(!!e.data.open, e.data.position);
  });
  (document.body || document.documentElement).appendChild(frame);
})();
