/* Arc carousel.
   Cards are laid out on a large-radius ring: every card is rotated about a
   shared origin so the row forms an arc that is tallest at the centre and
   scales down toward the rim. Drag / arrow keys move the ring; focusing a
   card brings it to the centre. All cards stay real links and stay in the
   tab order, so the row is fully keyboard reachable.

   Exposes window.CPArcCarousel.init(root) so a page that fills the row from
   JSON can initialise it after rendering. */
(function () {
  "use strict";

  function init(root) {
    var ring = root.querySelector("[data-arc-ring]");
    if (!ring) return;

    // Unwrap any slots from a previous pass so re-rendering is safe.
    Array.prototype.slice.call(ring.querySelectorAll(".arc-carousel__slot")).forEach(function (slot) {
      while (slot.firstChild) ring.insertBefore(slot.firstChild, slot);
      slot.remove();
    });

    var cards = Array.prototype.slice.call(ring.children).filter(function (el) {
      return el.nodeType === 1;
    });
    if (!cards.length) return;

    var cs = getComputedStyle(root);
    var step = parseFloat(cs.getPropertyValue("--arc-angle-step")) || 8;
    var minScale = 0.75;
    var empty = cards.length === 1 && cards[0].classList.contains("empty-state");

    var slots = cards.map(function (card, i) {
      var slot = document.createElement("div");
      slot.className = "arc-carousel__slot";
      slot.dataset.index = String(i);
      ring.insertBefore(slot, card);
      slot.appendChild(card);
      card.style.transformOrigin = "50% 0";
      card.style.willChange = "transform";
      return slot;
    });

    var offset = 0;
    var target = 0;
    var animating = false;

    function apply(animateStep) {
      var k = animateStep ? 0.2 : 1;
      offset += (target - offset) * k;
      ring.style.transform = "rotate(" + offset.toFixed(3) + "deg)";
      slots.forEach(function (slot, i) {
        var angle = i * step;
        slot.style.setProperty("--slot-angle", angle + "deg");
        var effective = angle + offset;
        var s = empty ? 1 : Math.max(minScale, 1 - Math.abs(effective) * 0.0125);
        var card = slot.firstElementChild;
        if (card && !card.classList.contains("empty-state")) {
          card.style.transform = "scale(" + s.toFixed(4) + ")";
        }
      });
    }

    function animate() {
      if (animating) return;
      animating = true;
      (function loop() {
        apply(true);
        if (Math.abs(target - offset) > 0.05) {
          requestAnimationFrame(loop);
        } else {
          offset = target;
          apply(false);
          animating = false;
          root.classList.add("is-ready");
        }
      })();
    }

    // Published so the pointer/keyboard handlers - bound once - always drive
    // the most recent slot list after a re-render.
    root._arc = {
      slots: slots,
      step: step,
      getOffset: function () { return offset; },
      setOffset: function (v) { offset = v; target = v; apply(false); },
      setTarget: function (deg) { target = deg; animate(); },
      animate: animate,
      endDrag: function () { target = Math.round(offset / step) * step; animate(); },
      centreIndex: centre
    };

    // Centre the middle card at rest.
    var centre = Math.floor(slots.length / 2);
    offset = target = empty ? 0 : -centre * step;
    apply(false);
    root.classList.add("is-ready");

    if (root.dataset.arcBound !== "1") {
      root.dataset.arcBound = "1";

      var dragging = false, startX = 0, startOffset = 0, moved = 0;

      root.addEventListener("pointerdown", function (e) {
        if (e.button !== undefined && e.button !== 0) return;
        if (e.target.closest && e.target.closest(".empty-state")) return;
        dragging = true; moved = 0;
        startX = e.clientX; startOffset = root._arc.getOffset();
        root.classList.add("is-dragging");
        if (root.setPointerCapture) { try { root.setPointerCapture(e.pointerId); } catch (err) {} }
      });

      root.addEventListener("pointermove", function (e) {
        if (!dragging) return;
        var dx = e.clientX - startX;
        moved = Math.max(moved, Math.abs(dx));
        root._arc.setOffset(startOffset + dx * 0.055);
      });

      function endDrag() {
        if (!dragging) return;
        dragging = false;
        root.classList.remove("is-dragging");
        root._arc.endDrag();
      }
      root.addEventListener("pointerup", endDrag);
      root.addEventListener("pointercancel", endDrag);
      root.addEventListener("pointerleave", endDrag);

      root.addEventListener("click", function (e) {
        if (moved > 6) { e.preventDefault(); e.stopPropagation(); }
      }, true);

      root.addEventListener("focusin", function (e) {
        var slot = e.target.closest ? e.target.closest(".arc-carousel__slot") : null;
        if (!slot) return;
        root._arc.setTarget(-parseInt(slot.dataset.index, 10) * root._arc.step);
      });

      root.addEventListener("keydown", function (e) {
        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
        var arc = root._arc;
        var slot = document.activeElement && document.activeElement.closest
          ? document.activeElement.closest(".arc-carousel__slot") : null;
        var i = slot ? parseInt(slot.dataset.index, 10)
          : Math.round(-arc.getOffset() / arc.step);
        var next = Math.max(0, Math.min(arc.slots.length - 1, e.key === "ArrowRight" ? i - 1 : i + 1));
        arc.setTarget(-next * arc.step);
        var link = arc.slots[next].firstElementChild && arc.slots[next].firstElementChild.querySelector("a[href]");
        if (link) link.focus();
        e.preventDefault();
      });
    }
  }

  function boot() {
    Array.prototype.slice.call(document.querySelectorAll("[data-arc-carousel]")).forEach(init);
  }

  window.CPArcCarousel = { init: init, refresh: boot };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
