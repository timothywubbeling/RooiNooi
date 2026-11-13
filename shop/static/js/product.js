/* Product page: photo gallery, picking options, live price and "Add to bag". */
(function () {
  "use strict";

  var data = JSON.parse(document.getElementById("product-data").textContent);
  var form = document.getElementById("buy-form");
  var qtyInput = document.getElementById("qty");
  var unitEl = document.getElementById("unit-price");
  var totalEl = document.getElementById("line-total");
  var progress = document.getElementById("min-progress");
  var mainImg = document.getElementById("gallery-main");
  var thumbs = Array.prototype.slice.call(document.querySelectorAll(".thumb"));

  function showImage(id) {
    var thumb = thumbs.filter(function (t) { return t.dataset.imageId === String(id); })[0];
    if (!thumb || !mainImg) return;
    mainImg.src = thumb.dataset.src;
    thumbs.forEach(function (t) { t.classList.toggle("is-active", t === thumb); });
    if (thumb.scrollIntoView) thumb.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
  thumbs.forEach(function (t) { t.addEventListener("click", function () { showImage(t.dataset.imageId); }); });

  function qty() {
    var n = parseInt(qtyInput.value, 10);
    return isNaN(n) ? 1 : Math.max(1, Math.min(200, n));
  }

  /* What is picked right now, the unit price, and which required groups are still open. */
  function state() {
    var options = {}, missing = [], unit = data.price_cents, low = data.price_cents;
    data.groups.forEach(function (g) {
      if (g.kind === "text") {
        var input = form.elements["g" + g.id];
        var text = input ? input.value.trim() : "";
        if (text) { options[g.id] = text; unit += g.price_cents; low += g.price_cents; }
        else if (g.required) missing.push(g);
        return;
      }
      if (!g.values.length) return;
      var checked = form.querySelector('input[name="g' + g.id + '"]:checked');
      if (checked) {
        options[g.id] = parseInt(checked.value, 10);
        unit += parseInt(checked.dataset.price, 10) || 0;
        low += parseInt(checked.dataset.price, 10) || 0;
      } else if (g.required) {
        missing.push(g);
        low += Math.min.apply(null, g.values.map(function (v) { return v.price_cents; }));
      }
    });
    return { options: options, missing: missing, unit: unit, low: low };
  }

  function update() {
    var s = state(), n = qty();
    var priced = s.missing.some(function (g) {
      return g.kind === "choice" && g.values.some(function (v) { return v.price_cents !== g.values[0].price_cents; });
    });
    unitEl.textContent = priced ? "From " + RN.rands(s.low) : RN.rands(s.unit);
    totalEl.textContent = !s.missing.length && n > 1 ? "· " + RN.rands(s.unit * n) : "";
    data.groups.forEach(function (g) {
      var box = form.querySelector('[data-group="' + g.id + '"]');
      if (!box) return;
      var picked = box.querySelector("[data-picked]");
      var checked = box.querySelector("input[type=radio]:checked");
      if (picked) picked.textContent = checked ? checked.dataset.label : "";
      if (s.missing.indexOf(g) === -1) setError(box, "");
    });
    if (progress) {
      var inBag = RN.qtyOf(data.id);
      progress.textContent = inBag ? "You have " + inBag + " in your bag." : "";
    }
  }

  function setError(box, text) {
    var el = box.querySelector(".opt-error");
    box.classList.toggle("has-error", !!text);
    if (el) { el.textContent = text; el.hidden = !text; }
  }

  form.addEventListener("change", function (e) {
    if (e.target.dataset && e.target.dataset.image) showImage(e.target.dataset.image);
    update();
  });
  form.addEventListener("input", update);
  document.querySelectorAll("[data-step]").forEach(function (b) {
    b.addEventListener("click", function () {
      qtyInput.value = Math.max(1, Math.min(200, qty() + parseInt(b.dataset.step, 10)));
      update();
    });
  });
  qtyInput.addEventListener("blur", function () { qtyInput.value = qty(); update(); });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var s = state();
    if (s.missing.length) {
      s.missing.forEach(function (g) {
        var box = form.querySelector('[data-group="' + g.id + '"]');
        if (box) setError(box, g.kind === "text" ? "Please fill this in." : "Please choose one.");
      });
      var first = form.querySelector('[data-group="' + s.missing[0].id + '"]');
      if (first) first.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    RN.add(data.id, qty(), s.options);
    // Names differ per cracker, so clear text boxes ready for the next one.
    form.querySelectorAll('input[type="text"]').forEach(function (i) { i.value = ""; });
    qtyInput.value = 1;
    update();
    RN.open({ added: true });
  });

  document.addEventListener("bag:changed", update);
  document.addEventListener("bag:closed", update);
  update();
})();
