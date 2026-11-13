/* The bag: kept in this browser, priced by the server every time it is shown. */
(function () {
  "use strict";

  var KEY = "rooinooi.bag.v1";
  var memory = [];            // used when the browser blocks storage (private mode etc.)
  var drawer = document.getElementById("cart");
  var scrim = document.querySelector(".scrim");
  var body = document.getElementById("cart-body");
  var foot = document.getElementById("cart-foot");
  var added = document.getElementById("cart-added");
  var lastFocus = null;

  function load() {
    try {
      var raw = window.localStorage.getItem(KEY);
      var lines = raw ? JSON.parse(raw) : [];
      return Array.isArray(lines) ? lines : [];
    } catch (e) {
      return memory.slice();
    }
  }

  function save(lines) {
    memory = lines.slice();
    try { window.localStorage.setItem(KEY, JSON.stringify(lines)); } catch (e) { /* memory copy is enough */ }
    badge();
  }

  function keyFor(productId, options) {
    var keys = Object.keys(options || {}).sort();
    return productId + "|" + keys.map(function (k) { return k + "=" + String(options[k]); }).join("&");
  }

  function count() {
    return load().reduce(function (n, l) { return n + (parseInt(l.qty, 10) || 0); }, 0);
  }

  function qtyOf(productId) {
    return load().reduce(function (n, l) { return n + (l.product_id === productId ? (parseInt(l.qty, 10) || 0) : 0); }, 0);
  }

  function badge() {
    var n = count();
    document.querySelectorAll("[data-cart-count]").forEach(function (el) {
      el.textContent = n > 99 ? "99+" : String(n);
      el.hidden = n === 0;
    });
  }

  function add(productId, qty, options) {
    var lines = load();
    var key = keyFor(productId, options);
    var found = lines.filter(function (l) { return l.key === key; })[0];
    if (found) found.qty = Math.min(200, (parseInt(found.qty, 10) || 0) + qty);
    else lines.push({ key: key, product_id: productId, qty: qty, options: options || {} });
    save(lines);
  }

  function setQty(key, qty) {
    var lines = load();
    lines.forEach(function (l) { if (l.key === key) l.qty = Math.max(1, Math.min(200, qty)); });
    save(lines);
  }

  function remove(key) {
    save(load().filter(function (l) { return l.key !== key; }));
  }

  function clear() { save([]); }

  function quote() {
    return fetch("/api/quote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lines: load() })
    }).then(function (r) {
      if (!r.ok) throw new Error("quote failed");
      return r.json();
    });
  }

  function rands(cents) {
    cents = Math.round(cents || 0);
    var whole = cents % 100 === 0;
    var text = whole ? String(cents / 100) : (cents / 100).toFixed(2);
    var parts = text.split(".");
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, " ");
    return "R" + parts.join(".");
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  var ICON = {
    minus: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M5 12h14"/></svg>',
    plus: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
    bag: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 8h14l-1 12.5a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 20.5Z"/><path d="M9 10V6.5a3 3 0 0 1 6 0V10"/></svg>',
    alert: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/></svg>'
  };

  /* One bag line; `editable` adds the quantity buttons and remove link. */
  function lineHtml(l, editable) {
    var opts = (l.options || []).map(function (o) {
      return '<span class="line-opt">' + esc(o.group) + ": " + esc(o.value) + (o.price_cents ? " (+" + rands(o.price_cents) + ")" : "") + "</span>";
    }).join("");
    var controls = editable
      ? '<div class="line-bottom"><div class="qty">' +
          '<button type="button" data-line-step="-1" data-key="' + esc(l.key) + '" aria-label="One less"' + (l.qty <= 1 ? " disabled" : "") + ">" + ICON.minus + "</button>" +
          "<span>" + l.qty + "</span>" +
          '<button type="button" data-line-step="1" data-key="' + esc(l.key) + '" aria-label="One more">' + ICON.plus + "</button>" +
        '</div><button class="line-remove" type="button" data-remove="' + esc(l.key) + '">Remove</button></div>'
      : '<span class="line-opt">Qty ' + l.qty + (l.qty > 1 && !l.error ? " × " + rands(l.unit_cents) : "") + "</span>";
    return '<div class="line">' +
      '<span class="line-img">' + (l.image ? '<img src="' + esc(l.image) + '" alt="">' : "") + "</span>" +
      '<div class="line-main"><div class="line-top">' +
        (l.url ? '<a class="line-name" href="' + esc(l.url) + '">' + esc(l.name) + "</a>" : '<span class="line-name">' + esc(l.name) + "</span>") +
        '<span class="line-price">' + (l.error ? "" : rands(l.line_cents)) + "</span></div>" +
        opts + (l.error ? '<span class="line-error">' + esc(l.error) + "</span>" : "") + controls +
      "</div></div>";
  }

  function problemsHtml(q) {
    return (q.problems || []).filter(function (p) { return p.product_id !== null; }).map(function (p) {
      return '<p class="problem">' + ICON.alert + "<span>" + esc(p.message) + "</span></p>";
    }).join("");
  }

  function render(q) {
    if (!q.lines.length) {
      body.innerHTML = '<div class="drawer-empty">' + ICON.bag + "<p>Your bag is empty.</p>" +
        '<a class="btn btn-wine" href="/shop">Start shopping</a></div>';
      foot.hidden = true;
      return;
    }
    body.innerHTML = q.lines.map(function (l) { return lineHtml(l, true); }).join("");
    document.getElementById("cart-problems").innerHTML = problemsHtml(q);
    document.getElementById("cart-subtotal").textContent = rands(q.subtotal_cents);
    var checkout = document.getElementById("cart-checkout");
    checkout.classList.toggle("is-disabled", !q.ok);
    checkout.setAttribute("aria-disabled", q.ok ? "false" : "true");
    foot.hidden = false;
  }

  function refresh() {
    if (!load().length) { render({ lines: [] }); return Promise.resolve(); }
    return quote().then(render).catch(function () {
      body.innerHTML = '<div class="drawer-empty"><p>Could not load your bag. Check your connection and try again.</p></div>';
    });
  }

  function open(opts) {
    if (!drawer) return;
    lastFocus = document.activeElement;
    added.hidden = !(opts && opts.added);
    drawer.hidden = false;
    scrim.hidden = false;
    document.body.style.overflow = "hidden";
    drawer.querySelector(".drawer-head .icon-btn").focus();
    if (!body.innerHTML) body.innerHTML = '<p class="muted" style="padding:20px 0">Loading…</p>';
    refresh();
  }

  function close() {
    if (!drawer || drawer.hidden) return;
    drawer.hidden = true;
    scrim.hidden = true;
    document.body.style.overflow = "";
    if (lastFocus && lastFocus.focus) lastFocus.focus();
    document.dispatchEvent(new CustomEvent("bag:closed"));
  }

  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-open-cart], [data-close-cart], [data-line-step], [data-remove], #cart-checkout");
    if (!t) return;
    if (t.matches("[data-open-cart]")) { e.preventDefault(); open(); }
    else if (t.matches("[data-close-cart]")) { e.preventDefault(); close(); }
    else if (t.matches("[data-line-step]")) {
      var line = load().filter(function (l) { return l.key === t.dataset.key; })[0];
      if (line) { setQty(line.key, line.qty + parseInt(t.dataset.lineStep, 10)); refresh(); }
      document.dispatchEvent(new CustomEvent("bag:changed"));
    } else if (t.matches("[data-remove]")) {
      remove(t.dataset.remove);
      refresh();
      document.dispatchEvent(new CustomEvent("bag:changed"));
    } else if (t.id === "cart-checkout" && t.getAttribute("aria-disabled") === "true") {
      e.preventDefault();
      var p = document.querySelector("#cart-problems .problem");
      if (p) p.scrollIntoView({ block: "nearest" });
    }
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
  window.addEventListener("storage", function (e) { if (e.key === KEY) badge(); });

  window.RN = { load: load, add: add, remove: remove, clear: clear, quote: quote, open: open, close: close,
                refresh: refresh, rands: rands, esc: esc, count: count, qtyOf: qtyOf, lineHtml: lineHtml,
                problemsHtml: problemsHtml };
  badge();
})();
