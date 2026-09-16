/* Header: mobile menu toggle, active-section underline, escape/focus handling. */
(function () {
  "use strict";

  function boot() {
    var header = document.querySelector(".header");
    var toggle = document.querySelector(".header__menu");
    var menu = document.querySelector(".mobile-menu");
    if (!header || !toggle || !menu) return;

    function close() {
      menu.classList.remove("is-open");
      header.classList.remove("is-menu-open");
      toggle.setAttribute("aria-expanded", "false");
      var first = menu.querySelector("a");
      if (first) first.removeAttribute("tabindex");
      Array.prototype.forEach.call(menu.querySelectorAll("a"), function (a) {
        a.setAttribute("tabindex", "-1");
      });
    }

    function open() {
      menu.classList.add("is-open");
      header.classList.add("is-menu-open");
      toggle.setAttribute("aria-expanded", "true");
      Array.prototype.forEach.call(menu.querySelectorAll("a"), function (a) {
        a.removeAttribute("tabindex");
      });
      var first = menu.querySelector("a");
      if (first) first.focus();
    }

    toggle.addEventListener("click", function () {
      if (menu.classList.contains("is-open")) close();
      else open();
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && menu.classList.contains("is-open")) {
        close();
        toggle.focus();
      }
    });

    // Trap focus inside the open overlay.
    document.addEventListener("focusin", function (e) {
      if (menu.classList.contains("is-open") && !menu.contains(e.target) && e.target !== toggle) {
        var first = menu.querySelector("a");
        if (first) first.focus();
      }
    });

    close();

    // Active-page underline follows in-page sections.
    var links = Array.prototype.slice.call(document.querySelectorAll('.nav-link[href^="#"]'));
    if (!links.length || !("IntersectionObserver" in window)) return;
    var map = {};
    links.forEach(function (a) { map[a.getAttribute("href").slice(1)] = a; });
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        var link = map[entry.target.id];
        if (!link) return;
        if (entry.isIntersecting) {
          links.forEach(function (a) { a.removeAttribute("aria-current"); });
          link.setAttribute("aria-current", "page");
        }
      });
    }, { rootMargin: "-45% 0px -50% 0px" });
    Object.keys(map).forEach(function (id) {
      var el = document.getElementById(id);
      if (el) io.observe(el);
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
