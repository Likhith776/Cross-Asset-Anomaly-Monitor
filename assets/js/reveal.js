/* Reveal-on-scroll.
   Mirrors the reference's pattern: elements start at opacity 0 /
   translateY(10%) and are switched by a ready class, driven here by an
   IntersectionObserver instead of a scroll library. */
(function () {
  "use strict";
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  var nodes = function () { return Array.prototype.slice.call(document.querySelectorAll("[data-reveal]")); };

  function show(el) {
    var delay = parseFloat(el.getAttribute("data-reveal-delay") || "0");
    if (delay > 0) {
      setTimeout(function () { el.classList.add("is-reveal-ready"); }, delay);
    } else {
      el.classList.add("is-reveal-ready");
    }
  }

  function init() {
    var els = nodes();
    if (reduce.matches || !("IntersectionObserver" in window)) {
      els.forEach(function (el) { el.classList.add("is-reveal-ready"); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          show(entry.target);
          io.unobserve(entry.target);
        }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.05 });
    els.forEach(function (el) { io.observe(el); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.CPReveal = { refresh: function () { nodes().forEach(function (el) {
    if (!el.classList.contains("is-reveal-ready")) el.classList.add("is-reveal-ready");
  }); } };
})();
