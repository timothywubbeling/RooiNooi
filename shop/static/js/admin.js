/* Shared admin bits: toast, "are you sure?" forms, JSON posts, the shop open/paused switch. */
(function () {
  "use strict";

  var toastEl = document.getElementById("toast");
  var timer = null;
  function toast(text, bad) {
    if (!toastEl) return;
    toastEl.textContent = text;
    toastEl.classList.toggle("is-bad", !!bad);
    toastEl.classList.add("is-on");
    clearTimeout(timer);
    timer = setTimeout(function () { toastEl.classList.remove("is-on"); }, 3200);
  }
  window.adminToast = toast;

  window.postJSON = function (url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": window.CSRF || "" },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (r.status === 401) { window.location.reload(); }
        if (!r.ok) throw new Error(d.error || "Something went wrong.");
        return d;
      });
    });
  };

  document.addEventListener("submit", function (e) {
    var form = e.target;
    var question = form.getAttribute("data-confirm");
    if (question && !window.confirm(question)) { e.preventDefault(); return; }
    var btn = form.querySelector('button[type="submit"]');
    if (btn && !form.id) btn.classList.add("is-busy");
  });

  var open = document.getElementById("store-open");
  if (open) open.addEventListener("change", function () {
    var want = open.checked;
    window.postJSON("/admin/api/store-open", { open: want }).then(function (d) {
      document.getElementById("open-label").textContent = d.open ? "Taking orders" : "Orders paused";
      document.getElementById("open-hint").textContent = d.open ? "Customers can check out." : "People can browse but cannot check out.";
      var badge = open.closest(".open-card").querySelector(".row-icon");
      badge.className = "row-icon " + (d.open ? "ok" : "warn");
      toast(d.open ? "The shop is taking orders." : "Orders are paused.");
    }).catch(function (err) {
      open.checked = !want;
      toast(err.message, true);
    });
  });

  /* Big phone photos are shrunk in the browser before they are sent (inputs with data-shrink). */
  function shrink(file, maxSide) {
    return new Promise(function (resolve) {
      if (!/^image\/(jpeg|png|webp|heic|heif)/i.test(file.type)) { resolve(file); return; }
      var url = URL.createObjectURL(file);
      var image = new Image();
      image.onload = function () {
        var scale = Math.min(1, maxSide / Math.max(image.naturalWidth, image.naturalHeight));
        if (scale === 1 && file.size < 700 * 1024) { URL.revokeObjectURL(url); resolve(file); return; }
        var canvas = document.createElement("canvas");
        canvas.width = Math.round(image.naturalWidth * scale);
        canvas.height = Math.round(image.naturalHeight * scale);
        var ctx = canvas.getContext("2d");
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
        URL.revokeObjectURL(url);
        canvas.toBlob(function (blob) {
          resolve(blob ? new File([blob], "photo.jpg", { type: "image/jpeg" }) : file);
        }, "image/jpeg", 0.86);
      };
      image.onerror = function () { URL.revokeObjectURL(url); resolve(file); };
      image.src = url;
    });
  }
  document.querySelectorAll("input[type=file][data-shrink]").forEach(function (input) {
    input.addEventListener("change", function () {
      var file = input.files && input.files[0];
      if (!file || typeof DataTransfer !== "function") return;
      shrink(file, 1600).then(function (small) {
        if (small === file) return;
        var dt = new DataTransfer();
        dt.items.add(small);
        input.files = dt.files;
      });
    });
  });

  document.querySelectorAll("[data-copy]").forEach(function (b) {
    b.addEventListener("click", function () {
      var input = document.getElementById(b.getAttribute("data-copy"));
      if (!input) return;
      input.select();
      (navigator.clipboard ? navigator.clipboard.writeText(input.value) : Promise.reject())
        .then(function () { toast("Link copied."); })
        .catch(function () { document.execCommand && document.execCommand("copy"); toast("Link copied."); });
    });
  });
})();
