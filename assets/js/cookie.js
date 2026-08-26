/* Cookie / storage notice. Nothing is tracked: the only thing persisted is
   the fact that the notice was dismissed. */
(function () {
  "use strict";
  var KEY = "cam.cookie-notice.v1";

  function read() {
    try { return window.localStorage.getItem(KEY); } catch (e) { return null; }
  }
  function write(v) {
    try { window.localStorage.setItem(KEY, v); } catch (e) {}
  }

  function boot() {
    var notice = document.querySelector(".cookie-notice");
    if (!notice) return;
    if (read() !== "dismissed") notice.classList.add("is-visible");

    var accept = notice.querySelector("[data-cookie-accept]");
    function dismiss() {
      write("dismissed");
      notice.classList.remove("is-visible");
    }
    if (accept) accept.addEventListener("click", dismiss);

    var openers = document.querySelectorAll("[data-cookie-open]");
    Array.prototype.forEach.call(openers, function (el) {
      el.addEventListener("click", function (e) {
        e.preventDefault();
        try { window.localStorage.removeItem(KEY); } catch (err) {}
        notice.classList.add("is-visible");
        var btn = notice.querySelector("[data-cookie-accept]");
        if (btn) btn.focus();
      });
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
