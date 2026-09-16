/* Custom cursor: two shapes in mix-blend-mode difference following the
   pointer at different lags, plus a label state. Only enabled on fine
   pointers; the native cursor stays untouched on touch / coarse pointers. */
(function () {
  "use strict";
  var fine = window.matchMedia("(hover: hover) and (pointer: fine)");
  if (!fine.matches) return;

  var big, small, labelWrap, labelEl;
  var pointer = { x: window.innerWidth / 2, y: window.innerHeight / 2 };
  var bigPos = { x: pointer.x, y: pointer.y };
  var smallPos = { x: pointer.x, y: pointer.y };
  var labelPos = { x: pointer.x, y: pointer.y };
  var visible = false;
  var running = false;

  function build() {
    big = document.createElement("div");
    big.className = "cursor__ball cursor__ball--big";
    big.setAttribute("aria-hidden", "true");
    small = document.createElement("div");
    small.className = "cursor__ball cursor__ball--small";
    small.setAttribute("aria-hidden", "true");
    labelWrap = document.createElement("div");
    labelWrap.className = "cursor__label-wrap";
    labelWrap.setAttribute("aria-hidden", "true");
    labelEl = document.createElement("div");
    labelEl.className = "cursor__label";
    labelWrap.appendChild(labelEl);
    document.body.appendChild(big);
    document.body.appendChild(small);
    document.body.appendChild(labelWrap);
    document.documentElement.classList.add("has-custom-cursor");
  }

  function tick() {
    bigPos.x += (pointer.x - bigPos.x) * 0.14;
    bigPos.y += (pointer.y - bigPos.y) * 0.14;
    smallPos.x += (pointer.x - smallPos.x) * 0.35;
    smallPos.y += (pointer.y - smallPos.y) * 0.35;
    labelPos.x += (pointer.x - labelPos.x) * 0.2;
    labelPos.y += (pointer.y - labelPos.y) * 0.2;
    big.style.transform = "translate3d(" + (bigPos.x - 15) + "px," + (bigPos.y - 17) + "px,0)";
    small.style.transform = "translate3d(" + (smallPos.x - 5) + "px," + (smallPos.y - 9) + "px,0)";
    labelWrap.style.transform = "translate3d(" + (labelPos.x - 60) + "px," + (labelPos.y - 55) + "px,0)";
    if (visible) requestAnimationFrame(tick);
    else running = false;
  }

  function start() {
    if (!running && visible) {
      running = true;
      requestAnimationFrame(tick);
    }
  }

  function setLabel(text) {
    if (text) {
      labelEl.textContent = text;
      labelWrap.classList.add("is-visible");
      big.classList.add("cursor__ball--label-active");
      small.style.opacity = "0";
    } else {
      labelEl.textContent = "";
      labelWrap.classList.remove("is-visible");
      big.classList.remove("cursor__ball--label-active");
      small.style.opacity = "1";
    }
  }

  var LABEL_TARGETS = "a[href], button, [data-cursor], input, select, summary, label";

  function labelFor(el) {
    if (!el || !el.closest) return null;
    var hit = el.closest(LABEL_TARGETS);
    if (!hit) return null;
    var explicit = hit.getAttribute && hit.getAttribute("data-cursor");
    if (explicit) return explicit;
    if (hit.closest(".arc-carousel")) return "DRAG";
    return null;
  }

  function onMove(e) {
    pointer.x = e.clientX;
    pointer.y = e.clientY;
    if (!visible) {
      visible = true;
      big.style.opacity = "1";
      small.style.opacity = "1";
      start();
    }
    setLabel(labelFor(e.target));
  }

  function onLeave() {
    visible = false;
    big.style.opacity = "0";
    small.style.opacity = "0";
    labelWrap.classList.remove("is-visible");
  }

  function boot() {
    build();
    window.addEventListener("pointermove", onMove, { passive: true });
    document.addEventListener("mouseleave", onLeave);
    document.addEventListener("pointerdown", function (e) { setLabel(labelFor(e.target)); });
    window.addEventListener("scroll", function () {
      if (visible) start();
    }, { passive: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
