/* Checkout: show the priced bag, collect details (and the PUDO locker), place the order. */
(function () {
  "use strict";

  var form = document.getElementById("co-form");
  var submit = document.getElementById("co-submit");
  var submitLabel = submit.getAttribute("data-label") || "Place order";
  var errorEl = document.getElementById("co-error");
  var linesEl = document.getElementById("co-lines");
  var problemsEl = document.getElementById("co-problems");
  var addressField = document.getElementById("address-field");
  var lockerField = document.getElementById("locker-field");
  var lockerInput = document.getElementById("locker-input");
  var lockerResults = document.getElementById("locker-results");
  var lockerPicked = document.getElementById("locker-picked");
  var quote = null;
  var busy = false;
  var lockers = null;          // [[code, name, address, town], ...] once loaded
  var lockersLoading = false;

  function way() {
    var r = form.querySelector('input[name="fulfilment"]:checked');
    if (!r) return null;
    return { id: r.value, fee: parseInt(r.dataset.fee, 10) || 0, quote: r.dataset.quote === "1", needs: r.dataset.needs || "" };
  }

  function renderTotals() {
    var w = way();
    var sub = quote ? quote.subtotal_cents : 0;
    var fee = w && !w.quote ? w.fee : 0;
    var label = w && w.id === "pudo" ? "PUDO" : "Delivery";
    document.querySelector("#co-delivery").previousElementSibling.textContent = w && w.id !== "collect" ? label : "Delivery";
    document.getElementById("co-subtotal").textContent = RN.rands(sub);
    document.getElementById("co-delivery").textContent = !w ? "–" : w.id === "collect" ? "Free (collect)" : w.quote ? "To be confirmed" : RN.rands(w.fee);
    document.getElementById("co-total").textContent = RN.rands(sub + fee) + (w && w.quote ? " + delivery" : "");
    addressField.hidden = !(w && w.needs === "address");
    lockerField.hidden = !(w && w.needs === "locker");
    if (w && w.needs === "locker") loadLockers();
  }

  function render() {
    if (!quote || !quote.lines.length) {
      linesEl.innerHTML = '<p class="muted">Your bag is empty. <a href="/shop">Go shopping</a></p>';
      problemsEl.innerHTML = "";
      submit.disabled = true;
      renderTotals();
      return;
    }
    linesEl.innerHTML = quote.lines.map(function (l) { return RN.lineHtml(l, false); }).join("");
    problemsEl.innerHTML = RN.problemsHtml(quote) +
      (quote.ok ? "" : '<p class="problem"><span>Please fix your bag before ordering. <a href="#" data-open-cart>Open your bag</a></span></p>');
    submit.disabled = !quote.ok || !window.SHOP_OPEN || busy;
    renderTotals();
  }

  function refresh() {
    if (!RN.load().length) { quote = { lines: [], ok: false, subtotal_cents: 0, problems: [] }; render(); return; }
    RN.quote().then(function (q) { quote = q; render(); }).catch(function () {
      linesEl.innerHTML = '<p class="muted">Could not load your bag. Check your connection and reload the page.</p>';
    });
  }

  /* ── PUDO locker picker (only when the shop is connected to PUDO) ── */
  function loadLockers() {
    if (!lockerResults || lockers || lockersLoading) return;
    lockersLoading = true;
    lockerInput.placeholder = "Loading PUDO lockers…";
    fetch("/api/pudo/lockers").then(function (r) { return r.json(); }).then(function (d) {
      lockers = d.lockers || [];
      lockerInput.placeholder = lockers.length ? "Search by suburb, town or shopping centre" : "Type the locker name and area";
      if (!lockers.length) switchToTyping();
    }).catch(function () {
      lockers = [];
      switchToTyping();
    }).then(function () { lockersLoading = false; });
  }

  function switchToTyping() {
    // No list available: let the customer type the locker instead.
    if (!lockerResults) return;
    lockerResults.remove();
    lockerResults = null;
    lockerInput.type = "text";
    lockerInput.name = "locker";
    form.querySelector('input[type="hidden"][name="locker"]').remove();
    lockerInput.placeholder = "e.g. Engen Garsfontein, Pretoria";
  }

  function searchLockers() {
    if (!lockerResults || !lockers) return;
    var q = lockerInput.value.trim().toLowerCase();
    if (q.length < 2) { lockerResults.innerHTML = ""; return; }
    var words = q.split(/\s+/);
    var hits = lockers.filter(function (l) {
      var hay = (l[0] + " " + l[1] + " " + l[2] + " " + l[3]).toLowerCase();
      return words.every(function (w) { return hay.indexOf(w) >= 0; });
    }).slice(0, 8);
    lockerResults.innerHTML = hits.length ? hits.map(function (l, i) {
      return '<button type="button" class="locker-option" role="option" data-i="' + lockers.indexOf(l) + '">' +
        "<strong>" + RN.esc(l[1]) + "</strong><small>" + RN.esc(l[2] || l[3]) + " · " + RN.esc(l[0]) + "</small></button>";
    }).join("") : '<p class="muted small">No lockers match that. Try a suburb or town name.</p>';
  }

  function pickLocker(l) {
    form.elements.locker.value = l[1] + " (" + l[0] + "), " + l[2];
    form.elements.locker_code.value = l[0];
    lockerPicked.innerHTML = "<span><strong>" + RN.esc(l[1]) + "</strong><br><small>" + RN.esc(l[2]) + "</small></span>" +
      '<button type="button" id="locker-change">Change</button>';
    lockerPicked.hidden = false;
    lockerInput.hidden = true;
    lockerResults.innerHTML = "";
    fieldError("locker", "");
  }

  if (lockerInput) {
    lockerInput.addEventListener("input", searchLockers);
    lockerInput.addEventListener("focus", loadLockers);
  }
  if (lockerField) lockerField.addEventListener("click", function (e) {
    var opt = e.target.closest(".locker-option");
    if (opt && lockers) { pickLocker(lockers[parseInt(opt.dataset.i, 10)]); return; }
    if (e.target.id === "locker-change") {
      form.elements.locker.value = "";
      form.elements.locker_code.value = "";
      lockerPicked.hidden = true;
      lockerInput.hidden = false;
      lockerInput.value = "";
      lockerInput.focus();
    }
  });

  /* ── errors + submit ── */
  function fieldError(name, message) {
    var input = form.elements[name];
    if (!input) return;
    var el = input.length && !input.tagName ? input[0] : input;
    var fld = el.closest ? el.closest(".fld") : null;
    if (fld) fld.classList.toggle("has-error", !!message);
  }

  function showError(message, field) {
    errorEl.textContent = message;
    errorEl.hidden = !message;
    if (field) {
      fieldError(field, message);
      var input = field === "locker" && lockerInput ? lockerInput : form.elements[field];
      if (input && input.focus && !input.hidden) input.focus();
    }
  }

  form.addEventListener("change", function (e) {
    if (e.target.name === "fulfilment") renderTotals();
  });
  form.addEventListener("input", function (e) { if (e.target.name) fieldError(e.target.name, ""); });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (busy) return;
    showError("");
    var f = form.elements;
    var w = way();
    if (f.name.value.trim().length < 2) return showError("Please enter your name.", "name");
    if (f.phone.value.replace(/\D/g, "").length < 9) return showError("Please enter a phone number we can WhatsApp or call.", "phone");
    if (!w) return showError("Please choose how you want your order.");
    if (w.needs === "address" && f.address.value.trim().length < 8) return showError("Please enter the delivery address.", "address");
    if (w.needs === "locker" && f.locker.value.trim().length < 3) return showError("Please choose the PUDO locker to send it to.", "locker");

    busy = true;
    submit.disabled = true;
    submit.textContent = w && submitLabel.indexOf("payment") >= 0 ? "Opening the payment page…" : "Placing your order…";
    fetch("/api/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lines: RN.load(),
        fulfilment: w.id,
        customer: {
          name: f.name.value, phone: f.phone.value, email: f.email.value, note: f.note.value,
          address: w.needs === "address" ? f.address.value : "",
          locker: w.needs === "locker" ? f.locker.value : "",
          locker_code: w.needs === "locker" ? f.locker_code.value : ""
        }
      })
    }).then(function (r) {
      return r.json().then(function (d) { return { ok: r.ok, data: d }; });
    }).then(function (res) {
      if (res.ok && res.data.redirect) {
        RN.clear();
        window.location.href = res.data.redirect;
        return;
      }
      busy = false;
      submit.textContent = submitLabel;
      if (res.data.quote) { quote = res.data.quote; render(); }
      var fields = res.data.fields || {};
      showError(res.data.error || "Something went wrong. Please try again.", Object.keys(fields)[0]);
      submit.disabled = !(quote && quote.ok);
    }).catch(function () {
      busy = false;
      submit.textContent = submitLabel;
      submit.disabled = false;
      showError("Could not reach the shop. Check your connection and try again.");
    });
  });

  document.addEventListener("bag:changed", refresh);
  document.addEventListener("bag:closed", refresh);
  refresh();
})();
