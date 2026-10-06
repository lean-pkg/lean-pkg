/* lean-pkg site additions: theme toggle + client-side search over search.json. No server, no build step. */
(function () {
  "use strict";
  var root = document.documentElement;
  function setTheme(t) { root.setAttribute("data-theme", t); try { localStorage.setItem("lp-theme", t); } catch (e) {} }
  function currentTheme() { return root.getAttribute("data-theme") || "light"; }
  function baseUrl() {
    var link = document.querySelector('link[rel="icon"][href*="assets/favicon-32.png"]');
    if (!link) return "";
    return link.getAttribute("href").replace(/assets\/favicon-32\.png.*$/, "");
  }
  function escapeHtml(s) { return s.replace(/[&<>"']/g, function (c) { return ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]; }); }
  function snippet(text, q) {
    var i = text.toLowerCase().indexOf(q.toLowerCase());
    var start = Math.max(0, i - 70), end = Math.min(text.length, i + q.length + 110);
    var s = (start > 0 ? "…" : "") + text.slice(start, end) + (end < text.length ? "…" : "");
    var re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "ig");
    return escapeHtml(s).replace(re, function (m) { return "<mark>" + m + "</mark>"; });
  }
  document.addEventListener("DOMContentLoaded", function () {
    var main = document.querySelector(".main-content");
    if (!main) return;
    var bar = document.createElement("div");
    bar.className = "lp-toolbar";
    var input = document.createElement("input");
    input.type = "search"; input.placeholder = "Search the documentation…"; input.setAttribute("aria-label", "Search the documentation");
    var btn = document.createElement("button");
    btn.type = "button"; btn.setAttribute("aria-label", "Toggle dark mode");
    function label() { btn.textContent = currentTheme() === "dark" ? "☀ Light" : "☾ Dark"; }
    label();
    btn.addEventListener("click", function () { setTheme(currentTheme() === "dark" ? "light" : "dark"); label(); });
    bar.appendChild(input); bar.appendChild(btn);
    main.insertBefore(bar, main.firstChild);
    var results = document.createElement("div"); results.className = "lp-results"; results.hidden = true;
    main.insertBefore(results, bar.nextSibling);
    var index = null, loading = null;
    function load() {
      if (index) return Promise.resolve(index);
      if (!loading) loading = fetch(baseUrl() + "search.json", { cache: "force-cache" }).then(function (r) { return r.json(); }).then(function (j) { index = j; return j; });
      return loading;
    }
    function render(q) {
      q = q.trim();
      if (q.length < 2) { results.hidden = true; results.innerHTML = ""; return; }
      load().then(function (pages) {
        var ql = q.toLowerCase();
        var hits = pages.map(function (p) {
          var t = (p.title || "").toLowerCase(), b = (p.text || "").toLowerCase();
          var score = (t.indexOf(ql) >= 0 ? 10 : 0) + (b.split(ql).length - 1);
          return { p: p, score: score };
        }).filter(function (h) { return h.score > 0; }).sort(function (a, b) { return b.score - a.score; }).slice(0, 12);
        if (!hits.length) { results.innerHTML = "<ul><li>No results for “" + escapeHtml(q) + "”.</li></ul>"; results.hidden = false; return; }
        results.innerHTML = "<ul>" + hits.map(function (h) {
          var inTitle = (h.p.title || "").toLowerCase().indexOf(ql) >= 0;
          return "<li><a href=\"" + escapeHtml(h.p.url) + "\">" + escapeHtml(h.p.title || h.p.url) + "</a>" +
            "<span class=\"lp-snippet\">" + snippet(inTitle ? (h.p.text || "") : (h.p.text || ""), q) + "</span></li>";
        }).join("") + "</ul>";
        results.hidden = false;
      }).catch(function () { results.innerHTML = "<ul><li>Search index unavailable.</li></ul>"; results.hidden = false; });
    }
    var timer = null;
    input.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(function () { render(input.value); }, 120); });
    input.addEventListener("keydown", function (e) { if (e.key === "Escape") { input.value = ""; render(""); } });
    document.addEventListener("keydown", function (e) { if (e.key === "/" && document.activeElement !== input && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) { e.preventDefault(); input.focus(); } });
  });
})();
