(function () {
  "use strict";

  var root = document.getElementById("contribute");
  if (!root) return;

  var csrf = document.querySelector('meta[name="csrf-token"]').content;
  var URLS = {
    upload: root.dataset.uploadUrl,
    del: root.dataset.deleteUrl,          // .../api/uploads/0
    vault: root.dataset.vaultUrl,
    create: root.dataset.createUrl,
    home: root.dataset.homeUrl
  };
  var MAX_BYTES = Number(root.dataset.maxBytes);
  var ALLOWED = [".pdf", ".jpg", ".jpeg", ".png", ".zip"];

  function $(id) { return document.getElementById(id); }
  var form = $("examForm");
  var dropzone = $("dropzone");
  var fileInput = $("fileInput");
  var cameraInput = $("cameraInput");
  var bayState = $("bayState");
  var dzError = $("dzError");
  var formError = $("formError");
  var progressWrap = $("progressWrap");
  var progressBar = $("progressBar");
  var progressPct = $("progressPct");
  var progressName = $("progressName");
  var fileCard = $("fileCard");
  var previewCard = $("previewCard");
  var saveBtn = $("saveBtn");
  var previewBtn = $("previewBtn");
  var dockHint = $("dockHint");
  var vaultInput = $("vaultId");

  var current = null;      // tep dang cho (da tai len, chua luu)
  var uploading = false;
  var saving = false;
  var submitted = false;

  /* ---------------- Tien ich ---------------- */
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function icon(name, cls) { return el("span", "material-symbols-outlined " + (cls || ""), name); }
  function ext(name) { var i = name.lastIndexOf("."); return i < 0 ? "" : name.slice(i).toLowerCase(); }
  function humanSize(n) {
    var u = ["B", "KB", "MB", "GB"], i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n.toFixed(0) : n.toFixed(1)) + " " + u[i];
  }
  function pad2(n) { return n < 10 ? "0" + n : String(n); }
  function parseJSON(text) { try { return JSON.parse(text); } catch (e) { return null; } }

  function setBay(text, state) { bayState.textContent = text; bayState.dataset.state = state; }
  function showDzError(msg) { dzError.textContent = msg || ""; dzError.hidden = !msg; }
  function showFormError(msg) { formError.textContent = msg || ""; formError.hidden = !msg; }

  /* ---------------- Chon / keo tha tep ---------------- */
  dropzone.addEventListener("click", function () { if (!uploading) fileInput.click(); });
  dropzone.addEventListener("keydown", function (e) {
    if ((e.key === "Enter" || e.key === " ") && !uploading) { e.preventDefault(); fileInput.click(); }
  });
  $("pickBtn").addEventListener("click", function () { if (!uploading) fileInput.click(); });
  $("cameraBtn").addEventListener("click", function () { if (!uploading) cameraInput.click(); });
  $("replaceBtn").addEventListener("click", function () { if (!uploading) fileInput.click(); });

  [fileInput, cameraInput].forEach(function (inp) {
    inp.addEventListener("change", function () {
      if (inp.files && inp.files[0]) handleFile(inp.files[0]);
      inp.value = "";
    });
  });

  ["dragenter", "dragover"].forEach(function (t) {
    dropzone.addEventListener(t, function (e) { e.preventDefault(); dropzone.classList.add("is-drag"); });
  });
  ["dragleave", "drop"].forEach(function (t) {
    dropzone.addEventListener(t, function (e) { e.preventDefault(); dropzone.classList.remove("is-drag"); });
  });
  dropzone.addEventListener("drop", function (e) {
    var files = e.dataTransfer && e.dataTransfer.files;
    if (!files || !files.length || uploading) return;
    handleFile(files[0]);
    if (files.length > 1) showDzError("Chỉ nhận một tệp mỗi lần. Đã lấy tệp đầu tiên; hãy dùng ZIP nếu có nhiều tệp.");
  });
  // Tranh trinh duyet mo tep khi tha ra ngoai vung nap
  window.addEventListener("dragover", function (e) { e.preventDefault(); });
  window.addEventListener("drop", function (e) { e.preventDefault(); });

  function validateClient(file) {
    if (ALLOWED.indexOf(ext(file.name)) < 0) return "Định dạng không được hỗ trợ. Chỉ nhận PDF, JPG, PNG hoặc ZIP.";
    if (file.size === 0) return "Tệp rỗng (0 byte).";
    if (file.size > MAX_BYTES) return "Tệp " + humanSize(file.size) + " vượt quá giới hạn " + humanSize(MAX_BYTES) + ".";
    return null;
  }

  /* ---------------- Tai len (XHR de co tien trinh) ---------------- */
  function handleFile(file) {
    showDzError("");
    var bad = validateClient(file);
    if (bad) { showDzError(bad); setBay("LỖI", "error"); return; }

    uploading = true;
    setBay("ĐANG TẢI", "busy");
    progressWrap.hidden = false;
    progressName.textContent = file.name;
    progressBar.style.width = "0%";
    progressPct.textContent = "0%";

    var fd = new FormData();
    fd.append("file", file);
    var xhr = new XMLHttpRequest();
    xhr.open("POST", URLS.upload);
    xhr.setRequestHeader("X-CSRF-Token", csrf);

    xhr.upload.onprogress = function (e) {
      if (!e.lengthComputable) return;
      var pct = Math.round(e.loaded / e.total * 100);
      progressBar.style.width = pct + "%";
      progressPct.textContent = pct >= 100 ? "ĐANG KIỂM TRA TỆP..." : pct + "%";
    };
    xhr.onerror = function () { finishUpload("Mất kết nối tới máy chủ. Vui lòng thử lại."); };
    xhr.onload = function () {
      var data = parseJSON(xhr.responseText);
      if (xhr.status >= 200 && xhr.status < 300 && data && data.ok) {
        var prev = current;
        current = data.file;
        finishUpload(null);
        renderFile();
        if (prev) discard(prev.id);
      } else if (data && data.error) {
        finishUpload(data.error);
      } else if (xhr.status === 413) {
        finishUpload("Tệp vượt quá giới hạn dung lượng của máy chủ.");
      } else {
        finishUpload("Tải lên thất bại (mã " + xhr.status + "). Hãy tải lại trang và thử lại.");
      }
    };
    xhr.send(fd);
  }

  function finishUpload(err) {
    uploading = false;
    progressWrap.hidden = true;
    if (err) { showDzError(err); setBay(current ? "ĐÃ NẠP" : "LỖI", current ? "done" : "error"); }
    else { showDzError(""); setBay("ĐÃ NẠP", "done"); }
  }

  /* ---------------- Hien thi thong tin tep ---------------- */
  function renderFile() {
    var f = current;
    fileCard.hidden = false;
    previewCard.hidden = false;

    $("fileIcon").textContent = f.kind === "pdf" ? "picture_as_pdf" : f.kind === "zip" ? "folder_zip" : "image";
    $("fileName").textContent = f.name;
    $("fileName").title = f.name;

    var metaParts = [f.size_h];
    if (f.kind === "pdf") metaParts.push(pad2(f.pages) + " trang");
    if (f.kind === "image" && f.width) metaParts.push(f.width + " x " + f.height + " px");
    if (f.kind === "zip") metaParts.push(f.zip_entries + " tệp");
    $("fileMeta").textContent = "Dung lượng: " + metaParts.join(" \u2022 ");

    if (f.kind === "pdf") {
      $("stat1Label").textContent = "SỐ LƯỢNG TRANG";
      $("stat1Value").textContent = pad2(f.pages) + " TRANG";
      $("stat2Label").textContent = "LOẠI NỘI DUNG";
      $("stat2Value").textContent = f.text_layer ? "PDF CÓ LỚP CHỮ" : "PDF SCAN (ẢNH)";
    } else if (f.kind === "image") {
      $("stat1Label").textContent = "ĐỘ PHÂN GIẢI";
      $("stat1Value").textContent = f.width + " x " + f.height;
      $("stat2Label").textContent = "ĐỊNH DẠNG";
      $("stat2Value").textContent = "ẢNH " + f.ext;
    } else {
      $("stat1Label").textContent = "SỐ TỆP TRONG GÓI";
      $("stat1Value").textContent = String(f.zip_entries);
      $("stat2Label").textContent = "ĐỊNH DẠNG";
      $("stat2Value").textContent = "GÓI ZIP";
    }
    $("stat3Value").textContent = f.sha_short;

    renderPreview(f);

    var openFull = $("openFullBtn");
    var link = f.preview_url || f.download_url;
    openFull.href = link;
    previewBtn.href = link;
    previewBtn.classList.remove("is-disabled");
    previewBtn.removeAttribute("aria-disabled");
    if (!f.preview_url) previewBtn.querySelector("span:last-child").textContent = "TẢI GÓI ZIP";
    else previewBtn.querySelector("span:last-child").textContent = "XEM TRƯỚC TÀI LIỆU";

    updateDockHint();
  }

  function renderPreview(f) {
    var grid = $("thumbGrid");
    var zipList = $("zipList");
    grid.textContent = "";
    zipList.textContent = "";

    if (f.kind === "zip") {
      $("previewTitle").textContent = "NỘI DUNG GÓI ZIP (" + f.zip_entries + " TỆP)";
      grid.hidden = true;
      zipList.hidden = false;
      f.zip_names.forEach(function (n) {
        var li = el("li");
        li.appendChild(icon("description", "icon-16"));
        li.appendChild(el("span", null, n));
        zipList.appendChild(li);
      });
      if (f.zip_entries > f.zip_names.length) {
        zipList.appendChild(el("li", null, "... và " + (f.zip_entries - f.zip_names.length) + " tệp khác"));
      }
      return;
    }

    grid.hidden = false;
    zipList.hidden = true;
    grid.classList.toggle("thumb-grid--single", f.kind === "image");
    var shown = f.thumbs.length;
    $("previewTitle").textContent = f.kind === "pdf"
      ? "XEM TRƯỚC CÁC TRANG ĐỀ THI (" + shown + (f.pages > shown ? "/" + f.pages : "") + " TRANG)"
      : "XEM TRƯỚC ẢNH ĐỀ THI";

    f.thumbs.forEach(function (src, i) {
      var a = el("a", "thumb");
      a.href = f.preview_url + (f.kind === "pdf" ? "#page=" + (i + 1) : "");
      a.target = "_blank";
      a.rel = "noopener";
      var box = el("div", "thumb__img");
      var img = document.createElement("img");
      img.src = src;
      img.alt = "Trang " + (i + 1);
      img.loading = "lazy";
      box.appendChild(img);
      a.appendChild(box);
      a.appendChild(el("span", null, f.kind === "pdf" ? "TRANG " + pad2(i + 1) : "ẢNH GỐC"));
      grid.appendChild(a);
    });
  }

  function resetFileUI() {
    current = null;
    fileCard.hidden = true;
    previewCard.hidden = true;
    previewBtn.href = "#";
    previewBtn.classList.add("is-disabled");
    previewBtn.setAttribute("aria-disabled", "true");
    setBay("SẴN SÀNG", "idle");
    updateDockHint();
  }

  function updateDockHint() {
    dockHint.textContent = "";
    if (!current) { dockHint.textContent = "Chưa có tệp nào được nạp."; return; }
    dockHint.appendChild(document.createTextNode("Sẵn sàng đóng gói: "));
    dockHint.appendChild(el("b", null, vaultInput.value + "." + current.ext.toLowerCase()));
  }

  /* ---------------- Xoa tep dang cho ---------------- */
  function discard(id) {
    return fetch(URLS.del.replace(/0$/, String(id)), {
      method: "DELETE",
      headers: { "X-CSRF-Token": csrf },
      credentials: "same-origin",
      keepalive: true
    }).catch(function () { /* se duoc don tu dong sau 24h */ });
  }

  $("removeBtn").addEventListener("click", function () {
    if (!current || uploading) return;
    var id = current.id;
    resetFileUI();
    showDzError("");
    discard(id);
  });

  $("cancelLink").addEventListener("click", function (e) {
    if (!current || submitted) return;
    e.preventDefault();
    var href = this.href;
    submitted = true; // bo canh bao roi trang
    discard(current.id).then(function () { window.location.href = href; });
  });

  window.addEventListener("beforeunload", function (e) {
    if (current && !submitted) { e.preventDefault(); e.returnValue = ""; }
  });

  /* ---------------- Ma kho ---------------- */
  $("regenBtn").addEventListener("click", function () {
    var btn = this;
    btn.classList.add("is-spin");
    fetch(URLS.vault, { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (d) { if (d && d.ok) { vaultInput.value = d.vault_id; updateDockHint(); } })
      .catch(function () { showFormError("Không tạo được mã mới. Vui lòng thử lại."); })
      .then(function () { btn.classList.remove("is-spin"); });
  });

  /* ---------------- Ma tran do kho ---------------- */
  var mInputs = { nb: $("nbTh"), vd: $("vd"), vdc: $("vdc") };
  function num(inp) { var n = parseInt(inp.value, 10); return isNaN(n) || n < 0 ? 0 : n; }
  function updateMatrix() {
    var a = num(mInputs.nb), b = num(mInputs.vd), c = num(mInputs.vdc);
    var sum = a + b + c;
    $("barNb").style.width = sum ? (a / sum * 100) + "%" : "0";
    $("barVd").style.width = sum ? (b / sum * 100) + "%" : "0";
    $("barVdc").style.width = sum ? (c / sum * 100) + "%" : "0";
    $("matrixTotal").textContent = sum ? "TỔNG " + sum + " CÂU" : "CHƯA NHẬP";
    var total = parseInt($("numQ").value, 10);
    var note = $("matrixNote");
    if (sum > 0 && total > 0 && sum !== total) {
      note.textContent = "Tổng ma trận (" + sum + ") phải bằng số câu hỏi (" + total + ").";
    } else {
      note.textContent = "";
    }
  }
  [mInputs.nb, mInputs.vd, mInputs.vdc, $("numQ")].forEach(function (i) {
    i.addEventListener("input", updateMatrix);
  });

  /* ---------------- Luu de thi ---------------- */
  function clearErrors() {
    showFormError("");
    Array.prototype.forEach.call(form.querySelectorAll("[data-error-for]"), function (n) { n.textContent = ""; });
    Array.prototype.forEach.call(form.querySelectorAll(".is-invalid"), function (n) { n.classList.remove("is-invalid"); });
  }

  function showFieldErrors(fields) {
    var first = null;
    Object.keys(fields).forEach(function (key) {
      var holder = form.querySelector('[data-error-for="' + key + '"]');
      if (holder) holder.textContent = fields[key];
      var input = form.elements[key];
      if (input && input.classList && !(input instanceof RadioNodeList)) {
        input.classList.add("is-invalid");
        if (!first) first = input;
      }
      if (key === "file") showDzError(fields[key]);
    });
    if (first && first.focus) first.focus();
  }

  function setSaving(on) {
    saving = on;
    saveBtn.disabled = on;
    var ic = saveBtn.querySelector(".material-symbols-outlined");
    ic.textContent = on ? "progress_activity" : "save";
    ic.classList.toggle("is-spin", on);
    $("saveLabel").textContent = on ? "ĐANG LƯU..." : "HOÀN TẤT & ĐƯA VÀO KHO LỚP IT-E6 (CTRL + S)";
  }

  function save() {
    if (saving || uploading) return;
    clearErrors();
    if (!current) {
      showDzError("Hãy tải lên tệp đề thi trước khi lưu.");
      setBay("LỖI", "error");
      dropzone.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    var fd = new FormData(form);
    fd.append("file_id", String(current.id));
    setSaving(true);

    fetch(URLS.create, {
      method: "POST",
      body: fd,
      headers: { "X-CSRF-Token": csrf },
      credentials: "same-origin"
    }).then(function (r) {
      return r.text().then(function (t) { return { status: r.status, data: parseJSON(t) }; });
    }).then(function (res) {
      if (res.data && res.data.ok) {
        submitted = true;
        window.location.href = res.data.redirect || URLS.home;
        return;
      }
      setSaving(false);
      if (res.data && res.data.fields) showFieldErrors(res.data.fields);
      showFormError((res.data && res.data.error) || "Không thể lưu đề thi (mã " + res.status + ").");
    }).catch(function () {
      setSaving(false);
      showFormError("Mất kết nối tới máy chủ. Vui lòng thử lại.");
    });
  }

  form.addEventListener("submit", function (e) { e.preventDefault(); save(); });

  document.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) {
      e.preventDefault();
      save();
    }
  });

  updateDockHint();
  updateMatrix();
})();