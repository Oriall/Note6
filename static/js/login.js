(function () {
  "use strict";

  var form = document.getElementById("loginForm");
  var pwd = document.getElementById("password");
  var eyeIcon = document.getElementById("pwdEyeIcon");
  var toggleBtn = document.getElementById("togglePwd");
  var capsAlert = document.getElementById("capsAlert");
  var submitBtn = document.getElementById("submitBtn");
  var statusBox = document.getElementById("authStatusBox");

  // Hien / an mat khau
  if (toggleBtn && pwd && eyeIcon) {
    toggleBtn.addEventListener("click", function () {
      var show = pwd.type === "password";
      pwd.type = show ? "text" : "password";
      eyeIcon.textContent = show ? "visibility_off" : "visibility";
      var label = show ? "Ẩn mật khẩu" : "Hiện mật khẩu";
      toggleBtn.title = label;
      toggleBtn.setAttribute("aria-label", label);
    });
  }

  // Canh bao Caps Lock
  function checkCaps(e) {
    if (!capsAlert || !e.getModifierState) return;
    capsAlert.classList.toggle("is-visible", e.getModifierState("CapsLock"));
  }
  if (pwd) {
    pwd.addEventListener("keyup", checkCaps);
    pwd.addEventListener("keydown", checkCaps);
    pwd.addEventListener("blur", function () {
      if (capsAlert) capsAlert.classList.remove("is-visible");
    });
  }

  // Trang thai dang gui: form van gui binh thuong len server
  if (form) {
    form.addEventListener("submit", function () {
      if (statusBox) statusBox.classList.add("is-visible");
      if (submitBtn) {
        // Tat nut o chu ky sau de khong chan viec gui form
        setTimeout(function () { submitBtn.disabled = true; }, 0);
      }
    });
  }

  // Khi quay lai bang nut Back (bfcache), khoi phuc trang thai nut
  window.addEventListener("pageshow", function (e) {
    if (e.persisted) {
      if (submitBtn) submitBtn.disabled = false;
      if (statusBox) statusBox.classList.remove("is-visible");
      if (pwd) pwd.value = "";
    }
  });
})();
