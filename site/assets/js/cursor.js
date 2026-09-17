/* Two difference-blended shapes follow at different speeds. Labels and
   hover scaling are independent of tracking so rapid target changes stay smooth. */
(function () {
  "use strict";
  var fine = window.matchMedia("(hover: hover) and (pointer: fine)");
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  var root = document.documentElement;
  var big, small, labelWrap, labelEl;
  var pointer = { x: 0, y: 0 };
  var bigPos = { x: 0, y: 0 };
  var smallPos = { x: 0, y: 0 };
  var labelPos = { x: 0, y: 0 };
  var enabled = false, visible = false, hasPointer = false, frame = 0;
  var needsHitTest = false, lastTime = 0, stateKey = "";
  var TARGETS = "a[href], button, input, textarea, select, summary, label, [data-hoverable], [data-cursor], [data-selectable], [role='button'], [role='tab'], [role='menuitem'], [role='checkbox'], [role='switch'], [role='slider']";
  var NATIVE = "canvas, iframe, textarea, [contenteditable]:not([contenteditable='false']), input:not([type]), input[type='text'], input[type='search'], input[type='email'], input[type='url'], input[type='tel'], input[type='password'], input[type='number']";
  var PASSIVE = "[data-cursor-passive], .cookie-notice, [data-cookie-notice], :disabled, [aria-disabled='true'], [inert]";

  function build() {
    big = document.createElement("div");
    big.className = "cursor__ball cursor__ball--big";
    big.innerHTML = '<svg width="30" height="30" viewBox="0 0 30 30" focusable="false"><path class="cursor__shape" d="M1,1 H29 V29 H1 Z"></path></svg>';
    small = document.createElement("div");
    small.className = "cursor__ball cursor__ball--small";
    small.innerHTML = '<svg width="10" height="10" viewBox="0 0 10 10" focusable="false"><rect class="cursor__shape" x="1" y="1" width="8" height="8" stroke-width="0"></rect></svg>';
    labelWrap = document.createElement("div");
    labelWrap.className = "cursor__label-wrap";
    labelWrap.innerHTML = '<span class="cursor__label-inner"><span class="cursor__label-chevron"><svg width="6" height="10" viewBox="0 0 6 10"><path d="M5 1L1 5l4 4" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"></path></svg></span><span class="cursor__label"></span><span class="cursor__label-chevron cursor__label-chevron--right"><svg width="6" height="10" viewBox="0 0 6 10"><path d="M1 1l4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"></path></svg></span></span>';
    labelEl = labelWrap.querySelector(".cursor__label");
    [big, small, labelWrap].forEach(function (el) {
      el.setAttribute("aria-hidden", "true");
      document.body.appendChild(el);
    });
  }

  function setState(hit) {
    var target = hit && hit.closest ? hit.closest(TARGETS) : null;
    if (hit && hit.closest(PASSIVE)) target = null;
    var carousel = target && target.closest(".arc-carousel.is-ready, .card-carousel--scrollable, .card-carousel--always-scroll, .gallery-scroller[data-cursor], .book-arc-carousel[data-cursor]");
    var text = carousel ? "DRAG" : target && target.getAttribute("data-cursor") || "";
    text = text.toUpperCase();
    var color = !carousel && text && target.getAttribute("data-cursor-color") || "";
    var hideSmall = !!(target && target.hasAttribute("data-cursor-hide-small"));
    var key = [!!target, text, color, hideSmall].join("|");
    if (key === stateKey) return;
    stateKey = key;
    big.classList.toggle("is-hovered", !!target);
    big.classList.toggle("cursor__ball--label-active", !!text);
    small.classList.toggle("is-hidden", hideSmall);
    labelWrap.classList.toggle("cursor__label-wrap--drag", text === "DRAG");
    labelWrap.classList.toggle("is-visible", visible && !!text);
    if (text) labelEl.textContent = text;
    if (color) big.style.setProperty("--cursor-hover-color", color);
    else big.style.removeProperty("--cursor-hover-color");
  }

  function hide() {
    visible = false;
    root.classList.remove("has-custom-cursor");
    if (big) {
      big.classList.remove("is-visible");
      small.classList.remove("is-visible");
      setState(null);
      labelWrap.classList.remove("is-visible");
    }
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    lastTime = 0;
  }

  function blocked() {
    return root.classList.contains("entrance-pending") ||
      (root.classList.contains("entrance-active") && !root.classList.contains("entrance-revealing"));
  }

  function updateTarget(hit) {
    // Chart canvases own their crosshair, drag and resize cursors. Text fields
    // retain the native insertion cursor instead of an oversized hover shape.
    if (!enabled || !hit || hit.closest(NATIVE) || hit.closest(".cookie-notice, [data-cookie-notice]") || blocked()) {
      hide();
      return;
    }
    if (!visible) {
      visible = true;
      bigPos.x = smallPos.x = labelPos.x = pointer.x;
      bigPos.y = smallPos.y = labelPos.y = pointer.y;
      paint();
      root.classList.add("has-custom-cursor");
      big.classList.add("is-visible");
      small.classList.add("is-visible");
      stateKey = "";
    }
    setState(hit);
  }

  function paint() {
    big.style.transform = "translate3d(" + (bigPos.x - 15) + "px," + (bigPos.y - 15) + "px,0)";
    small.style.transform = "translate3d(" + (smallPos.x - 5) + "px," + (smallPos.y - 7) + "px,0)";
    labelWrap.style.transform = "translate3d(" + (labelPos.x - 60) + "px," + (labelPos.y - 60) + "px,0)";
  }

  function tick(time) {
    frame = 0;
    if (needsHitTest && hasPointer) {
      needsHitTest = false;
      updateTarget(document.elementFromPoint(pointer.x, pointer.y));
    }
    if (!visible) return;
    var step = lastTime ? Math.min((time - lastTime) / (1000 / 60), 4) : 1;
    lastTime = time;
    var moving = false;
    [[bigPos, 0.2], [smallPos, 0.45], [labelPos, 0.2]].forEach(function (item) {
      var pos = item[0], rate = 1 - Math.pow(1 - item[1], step);
      pos.x += (pointer.x - pos.x) * rate;
      pos.y += (pointer.y - pos.y) * rate;
      if (Math.abs(pointer.x - pos.x) + Math.abs(pointer.y - pos.y) > 0.05) moving = true;
      else { pos.x = pointer.x; pos.y = pointer.y; }
    });
    paint();
    if (moving) frame = requestAnimationFrame(tick);
    else lastTime = 0;
  }

  function schedule() {
    if (!frame && enabled && hasPointer) frame = requestAnimationFrame(tick);
  }

  function onPointer(e) {
    if (e.pointerType === "touch") { hasPointer = false; hide(); return; }
    if (!enabled) return;
    pointer.x = e.clientX;
    pointer.y = e.clientY;
    hasPointer = true;
    updateTarget(e.target);
    schedule();
  }

  function recheck() {
    needsHitTest = true;
    schedule();
  }

  function leave() {
    hasPointer = false;
    hide();
  }

  function syncMedia() {
    enabled = fine.matches && !reduced.matches &&
      root.getAttribute("data-motion") !== "off" &&
      new URLSearchParams(window.location.search).get("motion") !== "off";
    if (enabled && !big) build();
    if (!enabled) leave();
  }

  function boot() {
    syncMedia();
    fine.addEventListener("change", syncMedia);
    reduced.addEventListener("change", syncMedia);
    document.addEventListener("pointermove", onPointer, { passive: true });
    document.addEventListener("pointerover", onPointer, { passive: true });
    document.addEventListener("pointerdown", onPointer, { passive: true });
    document.addEventListener("pointerup", recheck, { passive: true });
    document.addEventListener("pointercancel", leave);
    document.addEventListener("mouseleave", leave);
    document.addEventListener("pointerout", function (e) { if (!e.relatedTarget) leave(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Tab") leave(); });
    document.addEventListener("visibilitychange", function () { if (document.hidden) leave(); });
    document.addEventListener("scroll", recheck, { passive: true, capture: true });
    window.addEventListener("resize", recheck, { passive: true });
    window.addEventListener("blur", leave);
    window.addEventListener("pagehide", leave);
    window.addEventListener("cam:entrance-complete", recheck);
    // Filtered rows and paginated controls can replace the target without
    // a pointer event. Coalesce those hit tests into the next frame.
    new MutationObserver(function (records) {
      if (records.some(function (record) { return !labelWrap || !labelWrap.contains(record.target); })) recheck();
    }).observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true });
  else boot();
})();
