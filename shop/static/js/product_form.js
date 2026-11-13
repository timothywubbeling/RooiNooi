/* Product editor: photos (add, order, main, remove) and the options editor.
   Everything is sent with the normal form post when "Save product" is tapped. */
(function () {
  "use strict";

  var data = JSON.parse(document.getElementById("form-data").textContent);
  var form = document.getElementById("product-form");
  var tiles = document.getElementById("photo-tiles");
  var fileInput = document.getElementById("photo-input");
  var note = document.getElementById("photo-note");
  var groupsEl = document.getElementById("opt-groups");
  var picker = document.getElementById("picker");
  var pickerGrid = document.getElementById("picker-grid");
  var MAX_SIDE = 1600;
  var canSwap = typeof DataTransfer === "function";
  var dirty = false;
  var tmpN = 0;

  /* photos: {ref: "img:12" saved | "tmp:3" new | "file:abc.jpg" copied, url, blob?} */
  var photos = data.images.map(function (p) { return { ref: p.ref, url: p.url, file: p.file }; });
  var groups = (data.options || []).map(normaliseGroup);

  function normaliseGroup(g) {
    return {
      id: g.id || null, name: g.name || "", kind: g.kind === "text" ? "text" : "choice",
      required: g.required !== false, help: g.help || "", price: g.price || "0.00", max_length: g.max_length || 40,
      values: (g.values || []).map(function (v) {
        return { id: v.id || null, label: v.label || "", price: v.price || "0.00", image: v.image || "",
                 image_file: v.image_file || "", active: v.active !== false };
      })
    };
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function svg(paths) {
    return '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + paths + "</svg>";
  }
  var I = {
    left: svg('<path d="m15 18-6-6 6-6"/>'),
    star: svg('<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9Z"/>'),
    trash: svg('<path d="M3 6h18M8 6V4h8v2M6 6l1 15h10l1-15"/>'),
    up: svg('<path d="m6 15 6-6 6 6"/>'),
    down: svg('<path d="m6 9 6 6 6-6"/>'),
    image: svg('<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/>'),
    eye: svg('<path d="M2.5 12S6 5 12 5s9.5 7 9.5 7-3.5 7-9.5 7-9.5-7-9.5-7Z"/><circle cx="12" cy="12" r="3"/>'),
    eyeOff: svg('<path d="M3 3l18 18"/><path d="M10.6 5.1A10 10 0 0 1 12 5c5.5 0 9 5.5 9 7a11 11 0 0 1-2.6 3.6M6.3 6.3C4 7.8 3 10.6 3 12c0 1.5 3.5 7 9 7a9 9 0 0 0 4.4-1.1"/>'),
    plus: svg('<path d="M12 5v14M5 12h14"/>')
  };

  function toast(text, bad) { if (window.adminToast) window.adminToast(text, bad); }
  function photoByRef(ref) { return photos.filter(function (p) { return p.ref === ref; })[0]; }

  /* ── photos ─────────────────────────────────────────────────────────── */
  function renderPhotos() {
    tiles.innerHTML = photos.map(function (p, i) {
      var isNew = p.ref.indexOf("tmp:") === 0;
      return '<div class="photo-tile' + (i === 0 ? " is-main" : "") + '">' +
        '<img src="' + esc(p.url) + '" alt="">' +
        (i === 0 ? '<span class="main-tag">Main</span>' : isNew ? '<span class="new-tag">New</span>' : "") +
        '<div class="photo-tools">' +
          '<button type="button" data-photo="left" data-i="' + i + '" aria-label="Move earlier"' + (i === 0 ? " disabled" : "") + ">" + I.left + "</button>" +
          '<button type="button" data-photo="main" data-i="' + i + '" aria-label="Make this the main photo"' + (i === 0 ? " disabled" : "") + ">" + I.star + "</button>" +
          (isNew && !canSwap ? "" : '<button type="button" data-photo="remove" data-i="' + i + '" aria-label="Remove photo">' + I.trash + "</button>") +
        "</div></div>";
    }).join("");
  }

  tiles.addEventListener("click", function (e) {
    var b = e.target.closest("[data-photo]");
    if (!b) return;
    var i = parseInt(b.dataset.i, 10), act = b.dataset.photo;
    if (act === "left" && i > 0) { var t = photos[i - 1]; photos[i - 1] = photos[i]; photos[i] = t; }
    if (act === "main" && i > 0) { photos.unshift(photos.splice(i, 1)[0]); }
    if (act === "remove") {
      var gone = photos.splice(i, 1)[0];
      groups.forEach(function (g) { g.values.forEach(function (v) { if (v.image === gone.ref) { v.image = ""; v.image_file = ""; } }); });
      renderGroups();
    }
    dirty = true;
    renderPhotos();
  });

  function shrink(file) {
    return new Promise(function (resolve) {
      if (!/^image\/(jpeg|png|webp|heic|heif)/i.test(file.type)) { resolve(file); return; }
      var url = URL.createObjectURL(file);
      var image = new Image();
      image.onload = function () {
        var scale = Math.min(1, MAX_SIDE / Math.max(image.naturalWidth, image.naturalHeight));
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

  fileInput.addEventListener("change", function () {
    var files = Array.prototype.slice.call(fileInput.files || []);
    if (!files.length) return;
    if (!canSwap) {
      // Old browser: send the files as picked, in order.
      photos = photos.filter(function (p) { return !p.blob; });
      files.forEach(function (f, n) { photos.push({ ref: "new:" + n, url: URL.createObjectURL(f), blob: null, legacy: true }); });
      renderPhotos();
      dirty = true;
      return;
    }
    note.textContent = "Preparing photos…";
    note.hidden = false;
    Promise.all(files.map(shrink)).then(function (smaller) {
      smaller.forEach(function (f) {
        photos.push({ ref: "tmp:" + (tmpN++), url: URL.createObjectURL(f), blob: f });
      });
      fileInput.value = "";
      dirty = true;
      renderPhotos();
      renderGroups();
      note.textContent = "Photos ready. They are saved when you tap Save product.";
    });
  });

  /* ── options editor ─────────────────────────────────────────────────── */
  function thumbFor(v) {
    var p = v.image ? photoByRef(v.image) : null;
    var url = p ? p.url : (v.image_file ? data.media + v.image_file : "");
    return url ? '<img src="' + esc(url) + '" alt="">' : I.image;
  }

  function renderGroups() {
    if (!groups.length) {
      groupsEl.innerHTML = '<p class="hint">No options. Customers just choose how many they want.</p>';
      return;
    }
    groupsEl.innerHTML = groups.map(function (g, gi) {
      var isText = g.kind === "text";
      var values = isText ? "" :
        '<div class="opt-values">' + g.values.map(function (v, vi) {
          return '<div class="opt-value' + (v.active ? "" : " is-off") + '" data-gi="' + gi + '" data-vi="' + vi + '">' +
            '<button type="button" class="opt-thumb" data-act="v-photo" title="Photo for this choice" aria-label="Photo for ' + esc(v.label || "this choice") + '">' + thumbFor(v) + "</button>" +
            '<input class="input" data-v="label" value="' + esc(v.label) + '" maxlength="80" placeholder="Choice, e.g. Lip balm" aria-label="Choice name">' +
            '<span class="price"><span>R</span><input class="input" data-v="price" value="' + esc(v.price) + '" inputmode="decimal" aria-label="Extra cost"></span>' +
            '<div class="opt-value-tools">' +
              '<button type="button" class="icon-btn" data-act="v-toggle" title="' + (v.active ? "Available, tap to mark sold out" : "Sold out, tap to make available") + '" aria-label="' + (v.active ? "Mark sold out" : "Mark available") + '">' + (v.active ? I.eye : I.eyeOff) + "</button>" +
              '<button type="button" class="icon-btn" data-act="v-up" aria-label="Move up"' + (vi === 0 ? " disabled" : "") + ">" + I.up + "</button>" +
              '<button type="button" class="icon-btn" data-act="v-down" aria-label="Move down"' + (vi === g.values.length - 1 ? " disabled" : "") + ">" + I.down + "</button>" +
              '<button type="button" class="icon-btn" data-act="v-del" aria-label="Remove choice">' + I.trash + "</button>" +
            "</div></div>";
        }).join("") + "</div>" +
        '<button type="button" class="btn btn-sm btn-ghost" data-act="v-add" style="margin-top:6px">' + I.plus + "Add choice</button>";
      return '<div class="opt-group" data-gi="' + gi + '">' +
        '<div class="opt-group-head">' +
          '<input class="input" data-g="name" value="' + esc(g.name) + '" maxlength="60" placeholder="Option name, e.g. Filling" aria-label="Option name">' +
          '<div class="opt-group-tools">' +
            '<button type="button" class="icon-btn" data-act="g-up" aria-label="Move option up"' + (gi === 0 ? " disabled" : "") + ">" + I.up + "</button>" +
            '<button type="button" class="icon-btn" data-act="g-down" aria-label="Move option down"' + (gi === groups.length - 1 ? " disabled" : "") + ">" + I.down + "</button>" +
            '<button type="button" class="icon-btn" data-act="g-del" aria-label="Remove this option">' + I.trash + "</button>" +
          "</div></div>" +
        '<div class="opt-settings">' +
          '<select class="select" data-g="kind" aria-label="Type"><option value="choice"' + (isText ? "" : " selected") + '>Pick one</option><option value="text"' + (isText ? " selected" : "") + ">Text (e.g. a name)</option></select>" +
          '<label class="check"><input type="checkbox" data-g="required"' + (g.required ? " checked" : "") + "> Required</label>" +
          (isText ? '<span class="mini">Extra cost R<input class="input" data-g="price" value="' + esc(g.price) + '" inputmode="decimal"></span>' +
                    '<span class="mini">Max letters<input class="input" type="number" min="1" max="200" data-g="max_length" value="' + esc(g.max_length) + '"></span>' : "") +
        "</div>" +
        '<input class="input" data-g="help" value="' + esc(g.help) + '" maxlength="200" placeholder="Help text for customers (optional)" aria-label="Help text" style="margin-bottom:10px">' +
        values + "</div>";
    }).join("");
  }

  groupsEl.addEventListener("input", function (e) {
    var t = e.target, box = t.closest("[data-gi]");
    if (!box) return;
    var g = groups[parseInt(box.dataset.gi, 10)];
    dirty = true;
    if (t.dataset.g) {
      if (t.dataset.g === "required") g.required = t.checked;
      else if (t.dataset.g !== "kind") g[t.dataset.g] = t.value;
    }
    if (t.dataset.v) g.values[parseInt(box.dataset.vi, 10)][t.dataset.v] = t.value;
  });

  groupsEl.addEventListener("change", function (e) {
    var t = e.target, box = t.closest(".opt-group");
    if (!box) return;
    var g = groups[parseInt(box.dataset.gi, 10)];
    if (t.dataset.g === "required") { g.required = t.checked; dirty = true; }
    if (t.dataset.g === "kind") {
      g.kind = t.value;
      if (g.kind === "choice" && !g.values.length) g.values.push(blankValue());
      if (g.kind === "text") g.required = false;
      dirty = true;
      renderGroups();
    }
  });

  function blankValue() { return { id: null, label: "", price: "0.00", image: "", image_file: "", active: true }; }
  function swap(list, i, j) { if (j < 0 || j >= list.length) return; var t = list[i]; list[i] = list[j]; list[j] = t; }

  var pickTarget = null;
  groupsEl.addEventListener("click", function (e) {
    var b = e.target.closest("[data-act]");
    if (!b) return;
    var groupBox = b.closest(".opt-group");
    var gi = parseInt(groupBox.dataset.gi, 10), g = groups[gi];
    var valueBox = b.closest(".opt-value");
    var vi = valueBox ? parseInt(valueBox.dataset.vi, 10) : -1;
    switch (b.dataset.act) {
      case "g-up": swap(groups, gi, gi - 1); break;
      case "g-down": swap(groups, gi, gi + 1); break;
      case "g-del":
        if (!window.confirm("Remove the option “" + (g.name || "untitled") + "”?")) return;
        groups.splice(gi, 1); break;
      case "v-add":
        g.values.push(blankValue());
        renderGroups();
        var inputs = groupsEl.querySelectorAll('.opt-group[data-gi="' + gi + '"] [data-v="label"]');
        if (inputs.length) inputs[inputs.length - 1].focus();
        dirty = true;
        return;
      case "v-up": swap(g.values, vi, vi - 1); break;
      case "v-down": swap(g.values, vi, vi + 1); break;
      case "v-del": g.values.splice(vi, 1); break;
      case "v-toggle": g.values[vi].active = !g.values[vi].active; break;
      case "v-photo": openPicker(g.values[vi]); return;
    }
    dirty = true;
    renderGroups();
  });

  document.querySelectorAll("[data-add-group]").forEach(function (b) {
    b.addEventListener("click", function () {
      var kind = b.dataset.addGroup;
      groups.push({ id: null, name: "", kind: kind, required: kind === "choice", help: "", price: "0.00", max_length: 30,
                    values: kind === "choice" ? [blankValue()] : [] });
      dirty = true;
      renderGroups();
      var names = groupsEl.querySelectorAll('[data-g="name"]');
      names[names.length - 1].focus();
    });
  });

  /* photo picker for a choice */
  function openPicker(value) {
    pickTarget = value;
    pickerGrid.innerHTML = photos.length ? photos.map(function (p) {
      return '<button type="button" data-pick="' + esc(p.ref) + '" class="' + (value.image === p.ref ? "is-active" : "") + '"><img src="' + esc(p.url) + '" alt=""></button>';
    }).join("") : '<p class="hint" style="grid-column:1/-1">This product has no photos yet.</p>';
    picker.hidden = false;
  }
  function closePicker() { picker.hidden = true; pickTarget = null; }
  picker.addEventListener("click", function (e) {
    if (e.target === picker || e.target.closest("[data-close-picker]")) { closePicker(); return; }
    var b = e.target.closest("[data-pick]");
    if (!b || !pickTarget) return;
    pickTarget.image = b.dataset.pick;
    pickTarget.image_file = "";
    dirty = true;
    closePicker();
    renderGroups();
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !picker.hidden) closePicker(); });

  /* copy options from another product */
  var copyFrom = document.getElementById("copy-from");
  if (copyFrom) copyFrom.addEventListener("change", function () {
    var id = copyFrom.value;
    if (!id) return;
    fetch("/admin/api/products/" + id + "/options", { headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        copyFrom.value = "";
        if (!res.ok) { toast(res.data.error || "Could not copy those options.", true); return; }
        res.data.options.forEach(function (g) {
          g = normaliseGroup(g);
          g.values.forEach(function (v) {
            if (!v.image_file) { v.image = ""; return; }
            var same = photos.filter(function (p) { return p.file === v.image_file; })[0];
            if (same) { v.image = same.ref; return; }
            var ref = "file:" + v.image_file;
            photos.push({ ref: ref, url: data.media + v.image_file, file: v.image_file });
            v.image = ref;
          });
          groups.push(g);
        });
        dirty = true;
        renderPhotos();
        renderGroups();
        toast("Options copied. Check them, then tap Save product.");
      })
      .catch(function () { toast("Could not copy those options.", true); });
  });

  /* ── save ───────────────────────────────────────────────────────────── */
  var afterField = form.elements.after;
  form.querySelectorAll("[data-after]").forEach(function (b) {
    b.addEventListener("click", function () { afterField.value = b.dataset.after; });
  });

  form.addEventListener("input", function () { dirty = true; });
  var MAX_UPLOAD = 3.8 * 1024 * 1024;     // the web server takes about 4.5 MB per save
  form.addEventListener("submit", function (e) {
    var total = photos.reduce(function (n, p) { return n + (p.blob ? p.blob.size : 0); }, 0);
    if (total > MAX_UPLOAD) {
      e.preventDefault();
      note.textContent = "That is a lot of new photos for one save (" + (total / 1048576).toFixed(1) +
        " MB). Remove a few, tap Save product, then add the rest.";
      note.hidden = false;
      note.scrollIntoView({ block: "center" });
      toast("Too many new photos for one save.", true);
      return;
    }
    var map = {};
    if (canSwap) {
      var dt = new DataTransfer(), n = 0;
      photos.forEach(function (p) { if (p.blob) { dt.items.add(p.blob); map[p.ref] = "new:" + (n++); } });
      fileInput.files = dt.files;
    }
    form.elements.image_order.value = photos.map(function (p) { return map[p.ref] || p.ref; }).join(",");
    form.elements.options_json.value = JSON.stringify(groups.map(function (g) {
      return {
        id: g.id, name: g.name, kind: g.kind, required: g.required, help: g.help, price: g.price, max_length: g.max_length,
        values: g.values.map(function (v) {
          var ref = map[v.image] || v.image;
          if (ref && !photoByRef(v.image)) ref = "";
          return { id: v.id, label: v.label, price: v.price, image: ref, active: v.active };
        })
      };
    }));
    dirty = false;
    form.querySelectorAll('button[type="submit"]').forEach(function (b) { b.classList.add("is-busy"); });
  });
  window.addEventListener("beforeunload", function (e) {
    if (!dirty) return;
    e.preventDefault();
    e.returnValue = "";
  });

  renderPhotos();
  renderGroups();
})();
