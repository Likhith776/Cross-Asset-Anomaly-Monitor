/* Smooth scroll with native touch scrolling and reduced-motion fallback. */
(function () {
  "use strict";
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  var fine = window.matchMedia("(hover: hover) and (pointer: fine)");
  var doc = document.documentElement;
  var target = window.scrollY;
  var current = target;
  var raf = null;
  var locked = false;

  function enabled() {
    return fine.matches && !reduce.matches && doc.getAttribute("data-motion") !== "off" &&
      new URLSearchParams(location.search).get("motion") !== "off";
  }
  function clamp(v) { return Math.min(Math.max(v, 0), Math.max(0, doc.scrollHeight - innerHeight)); }
  function stop() {
    if (raf !== null) cancelAnimationFrame(raf);
    raf = null;
    target = current = window.scrollY;
  }
  function tick() {
    if (locked || !enabled()) { stop(); return; }
    current += (target - current) * .12;
    if (Math.abs(target - current) < .4) current = target;
    window.scrollTo(0, current);
    raf = current !== target ? requestAnimationFrame(tick) : null;
  }
  function setTarget(v) {
    target = clamp(v);
    if (raf === null) raf = requestAnimationFrame(tick);
  }
  window.addEventListener("wheel", function (e) {
    if (locked || !enabled() || e.ctrlKey || e.defaultPrevented) return;
    if (e.target.closest && e.target.closest("[data-native-scroll]")) return;
    e.preventDefault();
    setTarget(target + e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? innerHeight : 1));
  }, { passive: false });
  window.addEventListener("touchstart", stop, { passive: true });
  window.addEventListener("scroll", function () {
    if (raf === null) target = current = window.scrollY;
  }, { passive: true });
  window.addEventListener("resize", function () { target = clamp(target); });
  window.addEventListener("cam:nav-menu", function (e) { locked = e.detail.open; stop(); });

  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest('a[href^="#"]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    var hash = a.getAttribute("href");
    var el = hash.length > 1 && document.getElementById(decodeURIComponent(hash.slice(1)));
    if (!el || locked) return;
    e.preventDefault();
    var margin = parseFloat(getComputedStyle(el).scrollMarginTop) || 24;
    var header = document.querySelector("[data-nav-parity] .header");
    if (header) margin = Math.max(margin, header.getBoundingClientRect().bottom + 24);
    var top = el.getBoundingClientRect().top + window.scrollY - margin;
    if (enabled()) setTarget(top);
    else { stop(); window.scrollTo({ top: clamp(top), behavior: "instant" }); }
    if (history.replaceState) history.replaceState(null, "", hash);
  });
  function syncMedia() { stop(); doc.classList.toggle("lenis", enabled()); }
  reduce.addEventListener("change", syncMedia);
  fine.addEventListener("change", syncMedia);
  syncMedia();
})();
