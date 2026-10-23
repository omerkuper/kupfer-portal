/* Kupfer portal - the only script: tabs (open, switch, close, remember) and Ctrl K.
   Everything else is HTMX swapping server-rendered HTML. */
(function () {
  "use strict";
  var KEY = "kp-tabs", ON = "kp-tab-on";
  var bar = document.getElementById("tabs");
  var panes = document.getElementById("panes");
  var tpl = document.getElementById("tab-tpl");
  if (!bar || !panes || !tpl) return;

  function store(k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  function read(k) { try { return JSON.parse(sessionStorage.getItem(k) || "null"); } catch (e) { return null; } }

  function tabEl(id) { return bar.querySelector('.tab[data-tab="' + id + '"]'); }
  function paneEl(id) { return panes.querySelector('.pane[data-pane="' + id + '"]'); }
  function paneUrl(id, path) { return "/partials/tab/" + encodeURIComponent(id) + (path ? "?path=" + encodeURIComponent(path) : ""); }

  function save() {
    var list = [];
    bar.querySelectorAll(".tab[data-tab]").forEach(function (t) {
      if (t.dataset.tab !== "home") list.push({ id: t.dataset.tab, label: t.dataset.label, path: t.dataset.path || "" });
    });
    store(KEY, list);
  }

  function activate(id) {
    if (!tabEl(id)) id = "home";
    bar.querySelectorAll(".tab").forEach(function (t) {
      var on = t.dataset.tab === id;
      t.classList.toggle("on", on);
      t.querySelector("[role=tab]").setAttribute("aria-selected", on ? "true" : "false");
    });
    panes.querySelectorAll(".pane").forEach(function (p) { p.hidden = p.dataset.pane !== id; });
    document.querySelectorAll("[data-rail]").forEach(function (r) {
      if (r.dataset.rail === id) r.setAttribute("aria-current", "page"); else r.removeAttribute("aria-current");
    });
    store(ON, id);
    if (id === "home") document.body.dispatchEvent(new CustomEvent("kp:home"));
  }

  function load(pane, url) {
    if (window.htmx) window.htmx.ajax("GET", url, { target: pane, swap: "innerHTML" });
    else pane.setAttribute("data-pending", url);
  }

  function open(id, label, path, focus) {
    if (id === "home") { activate("home"); return; }
    var tab = tabEl(id);
    if (tab) {
      if (path && path !== tab.dataset.path) { tab.dataset.path = path; load(paneEl(id), paneUrl(id, path)); save(); }
      if (focus !== false) activate(id);
      return;
    }
    var node = tpl.content.firstElementChild.cloneNode(true);
    node.dataset.tab = id;
    node.dataset.label = label || id;
    node.dataset.path = path || "";
    var sel = node.querySelector("[data-tab-select]");
    sel.textContent = label || id;
    sel.dataset.tabSelect = id;
    node.querySelector("[data-tab-close]").dataset.tabClose = id;
    bar.appendChild(node);

    var pane = document.createElement("section");
    pane.className = "pane";
    pane.dataset.pane = id;
    pane.hidden = true;
    panes.appendChild(pane);
    load(pane, paneUrl(id, path));
    save();
    if (focus !== false) activate(id);
  }

  function close(id) {
    var tab = tabEl(id), pane = paneEl(id);
    var wasOn = tab && tab.classList.contains("on");
    var prev = tab && tab.previousElementSibling;
    if (tab) tab.remove();
    if (pane) pane.remove();
    save();
    if (wasOn) activate(prev && prev.dataset.tab ? prev.dataset.tab : "home");
  }

  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-open]");
    if (t) { e.preventDefault(); open(t.dataset.open, t.dataset.label, t.dataset.path || ""); return; }
    var s = e.target.closest("[data-tab-select]");
    if (s) { activate(s.dataset.tabSelect); return; }
    var c = e.target.closest("[data-tab-close]");
    if (c) { close(c.dataset.tabClose); return; }
    var r = e.target.closest("[data-reload]");
    if (r) {
      var f = r.closest(".tabview") && r.closest(".tabview").querySelector("iframe");
      if (f) { try { f.contentWindow.location.reload(); } catch (err) { f.src = f.src; } }
    }
  });

  document.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) {
      var box = document.getElementById("search");
      if (box && !box.disabled) { e.preventDefault(); activate("home"); box.focus(); }
    }
  });

  document.addEventListener("DOMContentLoaded", function () {
    panes.querySelectorAll("[data-pending]").forEach(function (p) { load(p, p.getAttribute("data-pending")); p.removeAttribute("data-pending"); });
  });

  (read(KEY) || []).forEach(function (t) { open(t.id, t.label, t.path, false); });
  activate(read(ON) || "home");
})();
