/* Checkout: show the priced bag, collect details, place the order. */
(function () {
  "use strict";

  var form = document.getElementById("co-form");
  var submit = document.getElementById("co-submit");
  var errorEl = document.getElementById("co-error");
  var linesEl = document.getElementById("co-lines");
  var problemsEl = document.getElementById("co-problems");
  var addressField = document.getElementById("address-field");
  var quote = null;
  var busy = false;

  function way() {
    var r = form.querySelector('input[name="fulfilment"]:checked');
    if (!r) return null;
    return { id: r.value, fee: parseInt(r.dataset.fee, 10) || 0, quote: r.dataset.quote === "1" };
  }

  function renderTotals() {
    var w = way();
    var sub = quote ? quote.subtotal_cents : 0;
    var fee = w && w.id === "delivery" && !w.quote ? w.fee : 0;
    document.getElementById("co-subtotal").textContent = RN.rands(sub);
    document.getElementById("co-delivery").textContent = !w ? "–" : w.id === "collect" ? "Free (collect)" : w.quote ? "To be confirmed" : RN.rands(w.fee);
    document.getElementById("co-total").textContent = RN.rands(sub + fee) + (w && w.id === "delivery" && w.quote ? " + delivery" : "");
    addressField.hidden = !(w && w.id === "delivery");
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

  function fieldError(name, message) {
    var input = form.elements[name];
    if (!input || !input.closest) return;
    var fld = input.closest(".fld");
    if (fld) fld.classList.toggle("has-error", !!message);
  }

  function showError(message, field) {
    errorEl.textContent = message;
    errorEl.hidden = !message;
    if (field) {
      fieldError(field, message);
      var input = form.elements[field];
      if (input && input.focus) input.focus();
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
    if (!w) return showError("Please choose collection or delivery.");
    if (w.id === "delivery" && f.address.value.trim().length < 8) return showError("Please enter the delivery address.", "address");

    busy = true;
    submit.disabled = true;
    submit.textContent = "Placing your order…";
    fetch("/api/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lines: RN.load(),
        fulfilment: w.id,
        customer: { name: f.name.value, phone: f.phone.value, email: f.email.value, address: f.address.value, note: f.note.value }
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
      submit.textContent = "Place order";
      if (res.data.quote) { quote = res.data.quote; render(); }
      var fields = res.data.fields || {};
      showError(res.data.error || "Something went wrong. Please try again.", Object.keys(fields)[0]);
      submit.disabled = !(quote && quote.ok);
    }).catch(function () {
      busy = false;
      submit.textContent = "Place order";
      submit.disabled = false;
      showError("Could not reach the shop. Check your connection and try again.");
    });
  });

  document.addEventListener("bag:changed", refresh);
  document.addEventListener("bag:closed", refresh);
  refresh();
})();
