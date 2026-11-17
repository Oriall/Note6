(function () {
  "use strict";

  var sidebar = document.getElementById("sidebar");
  var btn = document.getElementById("menuBtn");
  if (!sidebar || !btn) return;

  btn.addEventListener("click", function (e) {
    e.stopPropagation();
    sidebar.classList.toggle("is-open");
  });

  document.addEventListener("click", function (e) {
    if (sidebar.classList.contains("is-open") && !sidebar.contains(e.target)) {
      sidebar.classList.remove("is-open");
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") sidebar.classList.remove("is-open");
  });
})();