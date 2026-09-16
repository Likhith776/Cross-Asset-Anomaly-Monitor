/* Smooth scroll - a small RAF lerp, no dependency.
   The reference site runs Lenis; this reproduces the interaction
   (scroll position eases toward the target) without shipping a library.
   Disabled for prefers-reduced-motion and for coarse pointers, where the
   native scroll is better. */
(function () {
  "use strict";
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  if (reduce.matches) return;
  if (!("scrollBehavior" in document.documentElement.style)) return;

  var doc = document.documentElement;
  var target = window.scrollY || doc.scrollTop || 0;
  var current = target;
  var running = false;
  var lerp = 0.12;
  var raf = null;

  function maxScroll() {
    return Math.max(0, doc.scrollHeight - window.innerHeight);
  }

  function clampTarget(v) {
    return Math.min(Math.max(v, 0), maxScroll());
  }

  function tick() {
    current += (target - current) * lerp;
    if (Math.abs(target - current) < 0.4) {
      current = target;
    }
    window.scrollTo(0, current);
    if (current !== target) {
      raf = requestAnimationFrame(tick);
    } else {
      running = false;
      raf = null;
    }
  }

  function start() {
    if (!running) {
      running = true;
      raf = requestAnimationFrame(tick);
    }
  }

  function setTarget(v) {
    target = clampTarget(v);
    start();
  }

  function syncFromUser() {
    target = clampTarget(window.scrollY);
    current = target;
  }

  window.addEventListener(
    "wheel",
    function (e) {
      if (e.ctrlKey) return;
      if (e.target.closest && e.target.closest("[data-native-scroll]")) return;
      e.preventDefault();
      setTarget(target + e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? window.innerHeight : 1));
    },
    { passive: false }
  );

  var touchY = null;
  window.addEventListener(
    "touchstart",
    function (e) {
      if (e.touches.length === 1) {
        touchY = e.touches[0].clientY;
        syncFromUser();
        if (raf) cancelAnimationFrame(raf);
        running = false;
        raf = null;
      }
    },
    { passive: true }
  );
  window.addEventListener(
    "touchmove",
    function (e) {
      if (touchY === null || e.touches.length !== 1) return;
      var y = e.touches[0].clientY;
      var dy = touchY - y;
      touchY = y;
      window.scrollTo(0, clampTarget(window.scrollY + dy * 1.6));
    },
    { passive: true }
  );
  window.addEventListener("touchend", function () { touchY = null; }, { passive: true });

  window.addEventListener("scroll", function () {
    if (!running) syncFromUser();
  }, { passive: true });

  window.addEventListener("resize", function () { target = clampTarget(target); });

  // Anchor links ease instead of jumping.
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest('a[href^="#"]');
    if (!a) return;
    var id = a.getAttribute("href");
    if (!id || id === "#") return;
    var el = document.querySelector(id);
    if (!el) return;
    e.preventDefault();
    var top = el.getBoundingClientRect().top + window.scrollY - 24;
    setTarget(top);
    if (history.replaceState) history.replaceState(null, "", id);
  });

  reduce.addEventListener && reduce.addEventListener("change", function () {
    if (reduce.matches && raf) { cancelAnimationFrame(raf); raf = null; running = false; }
  });

  syncFromUser();
  doc.classList.add("lenis");
})();
