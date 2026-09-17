/* =====================================================================
   Motion layer — additive only.

   Reads the DOM that dashboard.js has already rendered and animates it.
   It never writes application state, never changes a value, and never
   touches the dashboard's data code. Remove this file (and motion.css)
   to drop the whole layer.

   Effect ids (see docs/design/MOTION-SPEC.md):
     M1 section entrance      M2 heading decode      M3 paragraph paint
     M4 panel development      M5 card count-up       M6 chart draw-on (CSS-free, set in dashboard.js)
     M7 table row stagger      M8 live pulse          M9 progress rail
     M11 hover depth (CSS only)
   ===================================================================== */
(function () {
  "use strict";

  var CHARSET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";   /* reference link-scramble charset */
  var DECODE_MS = 400;                          /* reference: 0.4s                 */
  var DECODE_MIN = DECODE_MS * 0.1;             /* reference: 0.04s                */
  var DECODE_SPAN = DECODE_MS * 0.7;            /* reference: 0.28s window         */
  var PAINT_PER_CHAR = 400;                     /* reference: 0.4s per char        */
  var PAINT_STAGGER = 20;                       /* reference: 0.02s                */
  var COUNT_MS = 700;

  var root = document.documentElement;
  if (root.getAttribute("data-motion") === "off") { window.__motionReady = true; return; }
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  if (reduce.matches) {
    root.setAttribute("data-motion", "off");
    window.__motionReady = true;
    return;
  }

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var raf = window.requestAnimationFrame || function (f) { return setTimeout(f, 16); };

  function skip(el, id) {
    var v = el.getAttribute("data-motion-skip");
    if (!v) return false;
    var parts = v.split(/\s+/);
    return parts.indexOf(id) !== -1 || parts.indexOf("all") !== -1;
  }

  function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }

  /* ---------------------------------------------------------------
     M1 / M5 / M7 — stagger indices
     The animation itself is CSS; this only supplies --i. Re-run when
     dashboard.js injects cards, rows or incidents.
     --------------------------------------------------------------- */
  function indexAll() {
    $$(".dash-section").forEach(function (el, i) {
      if (!skip(el, "M1")) el.style.setProperty("--i", String(i));
    });
    $$(".asset-card").forEach(function (el, i) {
      if (!skip(el, "M5")) el.style.setProperty("--i", String(Math.min(i, 12)));
    });
    var rows = $$("#alerts tbody tr");
    rows.forEach(function (el, i) {
      if (!skip(el, "M7")) el.style.setProperty("--i", String(i < 30 ? i : 30));
    });
    $$("#incidents-host .incident").forEach(function (el, i) {
      el.style.setProperty("--i", String(Math.min(i, 12)));
    });
  }

  /* ---------------------------------------------------------------
     M5 — price count-up.
     The rendered string is captured first and written back verbatim on
     the last frame, so the settled value is always exactly what the
     dashboard produced. Non-numeric values are skipped entirely.
     --------------------------------------------------------------- */
  function decimalPlaces(s) {
    var m = s.match(/\.(\d+)/);
    return m ? m[1].length : 0;
  }

  function countUp(el) {
    if (el.dataset.motionCounted === "1") return;
    var final = (el.textContent || "").trim();
    if (!/^-?[\d,]+(\.\d+)?$/.test(final)) return;
    var target = Number(final.replace(/,/g, ""));
    if (!isFinite(target)) return;
    var decimals = decimalPlaces(final);
    var grouping = final.indexOf(",") !== -1;
    var sign = target < 0 ? "-" : "";
    var abs = Math.abs(target);
    el.dataset.motionCounted = "1";
    var start = null;
    var format = new Intl.NumberFormat("en-US", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
      useGrouping: grouping
    });
    function frame(now) {
      if (start === null) start = now;
      var t = Math.min(1, (now - start) / COUNT_MS);
      if (t >= 1) {
        el.textContent = final;          /* exact original string */
        return;
      }
      el.textContent = sign + format.format(abs * easeOutCubic(t));
      raf(frame);
    }
    el.textContent = sign + format.format(0);
    raf(frame);
  }

  function runCountUps(scope) {
    $$(".asset-card__price", scope || document).forEach(function (el) {
      if (skip(el, "M5")) return;
      countUp(el);
    });
  }

  /* ---------------------------------------------------------------
     M2 — heading decode (the reference's link-scramble, moved from a
     hover gesture to a scroll event so it also works on touch).
     Original glyphs are restored from a stored copy, so the DOM is
     never left showing scrambled text.
     --------------------------------------------------------------- */
  function decode(el) {
    if (el.dataset.motionDecoded === "1" || skip(el, "M2")) return;
    var original = el.textContent;
    if (!original || !/[A-Za-z0-9]/.test(original)) return;
    el.dataset.motionDecoded = "1";
    el.classList.add("is-decoding");
    var chars = original.split("");
    var letters = chars.map(function (c) { return /[A-Za-z0-9]/.test(c); });
    var locked = letters.map(function () { return false; });
    var current = chars.slice();
    var start = performance.now();
    var locks = chars.map(function () {
      return DECODE_MIN + Math.random() * DECODE_SPAN;
    });

    (function step(now) {
      var elapsed = now - start;
      for (var i = 0; i < current.length; i++) {
        if (!letters[i] || locked[i]) continue;
        if (elapsed >= locks[i]) { locked[i] = true; current[i] = chars[i]; }
        else { current[i] = CHARSET[Math.floor(Math.random() * CHARSET.length)]; }
      }
      el.textContent = current.join("");
      if (elapsed < DECODE_MS) { raf(step); return; }
      el.textContent = original;
      el.classList.remove("is-decoding");
      el.dataset.motionDecoded = "1";
    })(start);
  }

  function watchDecode() {
    var targets = [];
    $$(".dash-section").forEach(function (sec) {
      var eyebrow = $(".section-header__eyebrow", sec);
      if (eyebrow) targets.push(eyebrow);
      var title = $(".section-header__title", sec);
      if (title && sec.id !== "hero") targets.push(title);
    });
    if (!targets.length || !("IntersectionObserver" in window)) return;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { decode(e.target); io.unobserve(e.target); }
      });
    }, { threshold: 0.25 });
    targets.forEach(function (t) { io.observe(t); });
  }

  /* ---------------------------------------------------------------
     M3 — hero paragraph paint. Character colour is driven by scroll
     progress with the reference's 0.6 scrub lag: characters resolve
     #333333 -> #ffffff, 0.4s per char, 0.02s stagger, linear.
     Colour only — the text is never hidden, split for layout, or
     removed from the accessibility tree.
     --------------------------------------------------------------- */
  var paint = null;

  function setupPaint() {
    /* Selected by structure, not by a marker attribute, so the dashboard
       markup stays untouched beyond the two motion asset tags. */
    var el = $("#hero .dash-section__note") || $("[data-motion-paint]");
    if (!el || skip(el, "M3")) return;
    var text = el.textContent;
    if (!text) return;
    el.textContent = "";
    var frag = document.createDocumentFragment();
    var chars = [];
    text.split("").forEach(function (c) {
      if (c === " ") {
        frag.appendChild(document.createTextNode(" "));
        return;
      }
      var span = document.createElement("span");
      span.className = "motion-char";
      span.textContent = c;
      chars.push(span);
      frag.appendChild(span);
    });
    el.appendChild(frag);
    paint = { el: el, chars: chars, progress: -1 };
    updatePaint();
  }

  function updatePaint() {
    if (!paint) return;
    var rect = paint.el.getBoundingClientRect();
    var vh = window.innerHeight || 1;
    /* start when the block's top passes 70% of the viewport,
       finish when it passes 30% (the reference's defaults) */
    var span = vh * 0.4;
    var p = (vh * 0.7 - rect.top) / span;
    p = Math.max(0, Math.min(1, p));
    if (Math.abs(p - paint.progress) < 0.002) return;
    paint.progress = p;
    var total = paint.chars.length;
    var visible = p * (total + (total * PAINT_STAGGER) / PAINT_PER_CHAR);
    for (var i = 0; i < total; i++) {
      var startAt = i * (PAINT_STAGGER / PAINT_PER_CHAR);
      var local = Math.max(0, Math.min(1, visible - startAt));
      /* linear, as in the reference */
      var c = Math.round(0x33 + (0xff - 0x33) * local);
      var ch = paint.chars[i];
      if (ch.dataset.v !== String(c)) {
        ch.dataset.v = String(c);
        ch.style.color = local >= 1 ? "#ffffff" : "rgb(" + c + "," + c + "," + c + ")";
      }
    }
    if (p >= 1) paint.el.classList.add("is-painted");
  }

  /* ---------------------------------------------------------------
     M4 — panel development. The reference animates the container width
     from 0; that would force ECharts to resize every frame, so the
     class flip drives a clip-path wipe plus a one-off surface scale.
     A failsafe reveals everything if anything goes wrong.
     --------------------------------------------------------------- */
  function watchPanels() {
    var panels = $$(".chart-wrap");
    if (!panels.length) return;
    /* `is-settled` releases the temporary overflow clip once the wipe and
       the surface scale have both finished (900ms wipe; 300ms delay +
       1500ms scale), so the ECharts tooltip is not clipped at rest. */
    function reveal(p) {
      if (p.classList.contains("is-inview")) return;
      p.classList.add("is-inview");
      setTimeout(function () { p.classList.add("is-settled"); }, 1900);
    }
    /* A sweep that reveals only panels actually inside the viewport. It is
       a backstop for the observer, not a blanket release: revealing a panel
       that is still thousands of pixels below the fold would spend its
       entrance before the reader ever sees it. */
    function inViewport(el) {
      var r = el.getBoundingClientRect();
      return r.top < window.innerHeight * 0.94 && r.bottom > 0;
    }
    function sweep() {
      panels.forEach(function (p) {
        if (!p.classList.contains("is-inview") && inViewport(p)) reveal(p);
      });
    }
    var queued = false;
    window.addEventListener("scroll", function () {
      if (queued) return;
      queued = true;
      raf(function () { queued = false; sweep(); });
    }, { passive: true });
    window.addEventListener("resize", sweep, { passive: true });
    setTimeout(sweep, 6000);
    if (!("IntersectionObserver" in window)) { sweep(); return; }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { reveal(e.target); io.unobserve(e.target); }
      });
    }, { rootMargin: "0px 0px -6% 0px", threshold: 0.08 });
    panels.forEach(function (p) {
      if (skip(p, "M4")) { reveal(p); return; }
      io.observe(p);
    });
    setTimeout(sweep, 6000);
  }

  /* ---------------------------------------------------------------
     M8 — live pulse. One ring expansion when the published timestamp
     changes, and a 400ms digit flicker on the stamp itself. The stamp
     is always restored to its exact original text.
     --------------------------------------------------------------- */
  function flicker(el, original) {
    var start = performance.now();
    var chars = original.split("");
    var letters = chars.map(function (c) { return /[0-9]/.test(c); });
    var locks = chars.map(function () { return DECODE_MIN + Math.random() * DECODE_SPAN; });
    (function step(now) {
      var elapsed = now - start;
      var out = chars.slice();
      for (var i = 0; i < out.length; i++) {
        if (letters[i] && elapsed < locks[i]) {
          out[i] = String(Math.floor(Math.random() * 10));
        }
      }
      el.textContent = out.join("");
      if (elapsed < DECODE_MS) { raf(step); return; }
      el.textContent = original;
    })(start);
  }

  function watchPulse() {
    var stamp = $("[data-updated]");
    var status = $(".cp-status--live");
    if (!stamp) return;
    var last = stamp.textContent;
    var seen = false;
    function pulse(text) {
      if (!status) return;
      status.classList.remove("is-pulsing");
      /* force a reflow so the animation restarts */
      void status.offsetWidth;
      status.classList.add("is-pulsing");
      setTimeout(function () { status.classList.remove("is-pulsing"); }, 950);
      if (seen && !skip(stamp, "M8")) flicker(stamp, text);
      seen = true;
    }
    new MutationObserver(function () {
      var text = stamp.textContent;
      if (text === last) return;
      last = text;
      if (/published|snapshot/i.test(text)) pulse(text);
    }).observe(stamp, { childList: true, characterData: true, subtree: true });
  }

  /* ---------------------------------------------------------------
     M9 — scroll progress rail. 1:1 with scroll position, no easing, so
     it tracks the finger.
     --------------------------------------------------------------- */
  function setupRail() {
    /* The rail is the only part of the layer that is visible at rest, so it
       gets its own opt-out: data-motion-skip="M9" (or "scroll") on <html>. */
    if (skip(root, "M9") || skip(root, "scroll")) return;
    var rail = document.createElement("div");
    rail.id = "motion-rail";
    rail.setAttribute("aria-hidden", "true");
    var bar = document.createElement("i");
    rail.appendChild(bar);
    document.body.appendChild(rail);
    var queued = false;
    function draw() {
      queued = false;
      var max = Math.max(1, root.scrollHeight - window.innerHeight);
      var p = Math.max(0, Math.min(1, (window.scrollY || root.scrollTop || 0) / max));
      bar.style.setProperty("--p", p.toFixed(4));
    }
    window.addEventListener("scroll", function () {
      if (queued) return;
      queued = true;
      raf(draw);
    }, { passive: true });
    window.addEventListener("resize", draw, { passive: true });
    draw();
  }

  /* ---------------------------------------------------------------
     Scroll binding for M3
     --------------------------------------------------------------- */
  function bindScroll() {
    if (!paint) return;
    var queued = false;
    window.addEventListener("scroll", function () {
      if (queued) return;
      queued = true;
      raf(function () { queued = false; updatePaint(); });
    }, { passive: true });
    window.addEventListener("resize", function () { paint.progress = -1; updatePaint(); }, { passive: true });
  }

  /* ---------------------------------------------------------------
     M12 — hover / focus decrypt ("encrypt and decrypt" on hover)

     Values taken from the reference's own module (link-scramble) and
     confirmed against its live behaviour:
       charset   A-Z only, so scrambled glyphs are always uppercase while
                 the original string keeps its own case ("BoTks" -> "Books")
       duration  400ms, one pass per hover, re-triggered on the next one
       locking   each character locks at random*0.7*400 + 0.1*400, i.e.
                 40ms-320ms, so short leading words resolve first
       other     every non-alphanumeric character is never touched:
                 spaces, "-", "=", "^", "&", "." and "->" stay put
       restore   the original string is written back verbatim on completion,
                 and immediately on pointerdown (so a click never navigates
                 while the label is mid-decrypt)

     Layout safety: the character COUNT never changes and the dashboard is
     set in a monospace face, so a scrambled label occupies exactly the same
     box as the real one. No reflow, no shift.

     Accessibility: the host's accessible name is pinned to the real label for
     the duration of the pass, so assistive technology never announces
     scrambled characters. The focus ring is untouched.

     Exposed as window.CPScramble so any future element can opt in with
     data-scramble="<inner selector>" without duplicating this code.
     --------------------------------------------------------------- */
  var SC_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  var SC_MS = 400;
  var SC_LOCK_MIN = 40;
  var SC_LOCK_SPAN = 280;
  var SC_BIND = 'a[href], button, [role="tab"], [role="menuitem"], tr[data-selectable]';
  var SC_SKIP = '.footer__social, [data-scramble-skip]';
  /* Controls whose accessible name comes from several children get an explicit
     label target rather than the whole control. Table rows are included: their
     symbol cell is the interactive label, but scrambling all seven cells of a
     row at once would be unreadable. */
  var SC_LABEL = [
    [".asset-card", ".asset-card__label"],
    ["tr[data-selectable]", "td:nth-child(2)"]
  ];
  var scRunning = new WeakMap();
  var scLastPointer = -1e9;
  var scActive = 0;
  var scMaxActive = 0;

  function scScrableable(ch) { return /[A-Za-z0-9]/.test(ch); }

  function scLabelFor(el) {
    var explicit = el.getAttribute("data-scramble");
    if (explicit) {
      var found = el.querySelector(explicit);
      if (found) return found;
    }
    for (var i = 0; i < SC_LABEL.length; i++) {
      if (el.matches(SC_LABEL[i][0])) {
        var t = el.querySelector(SC_LABEL[i][1]);
        if (t) return t;
      }
    }
    var kids = Array.prototype.slice.call(el.childNodes);
    if (kids.length === 1 && kids[0].nodeType === 3 && kids[0].nodeValue.trim()) return el;
    /* tagName is lowercase for inline SVG in an HTML document, so compare
       case-insensitively - otherwise an icon next to a wordmark hides the
       wordmark and the control is silently skipped. */
    var isIcon = function (n) {
      return n.tagName.toLowerCase() === "svg" || n.classList.contains("visually-hidden");
    };
    var els = kids.filter(function (n) { return n.nodeType === 1 && !isIcon(n); });
    var withText = els.filter(function (n) { return n.textContent.trim() && !n.querySelector("*"); });
    if (withText.length === 1 && els.length === 1) return withText[0];
    return null;
  }

  function scRun(host, node) {
    if (!node || skip(host, "M12")) return;
    var original = node.textContent;
    if (!original || !/[A-Za-z0-9]/.test(original)) return;
    var prev = scRunning.get(host);
    if (prev) prev.cancel();

    var hostLabel = (host.textContent || "").trim();
    var priorAria = host.getAttribute("aria-label");
    if (priorAria === null && hostLabel) host.setAttribute("aria-label", hostLabel);

    var chars = original.split("");
    var lockable = chars.map(scScrableable);
    var locks = chars.map(function () { return SC_LOCK_MIN + Math.random() * SC_LOCK_SPAN; });
    var shown = chars.slice();
    var start = null;
    var rafId = null;
    var done = false;

    function finish() {
      if (done) return;
      done = true;
      if (rafId) cancelAnimationFrame(rafId);
      node.textContent = original;
      if (priorAria === null) host.removeAttribute("aria-label");
      else host.setAttribute("aria-label", priorAria);
      scRunning.delete(host);
      scActive--;
    }

    function step(now) {
      if (start === null) start = now;
      var elapsed = now - start;
      for (var i = 0; i < shown.length; i++) {
        if (!lockable[i]) continue;
        shown[i] = elapsed >= locks[i]
          ? chars[i]
          : SC_CHARS[Math.floor(Math.random() * SC_CHARS.length)];
      }
      node.textContent = shown.join("");
      if (elapsed < SC_MS) { rafId = requestAnimationFrame(step); return; }
      finish();
    }

    scRunning.set(host, { cancel: finish });
    scActive++;
    if (scActive > scMaxActive) scMaxActive = scActive;
    rafId = requestAnimationFrame(step);
  }

  function scBind(root) {
    var fine = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
    $$(SC_BIND, root || document).forEach(function (el) {
      if (el.dataset.scrambleBound === "1") return;
      if (el.closest(SC_SKIP)) return;
      var node = scLabelFor(el);
      if (!node) return;
      el.dataset.scrambleBound = "1";
      if (fine) {
        el.addEventListener("mouseenter", function () { scRun(el, node); });
      }
      el.addEventListener("focus", function () {
        /* run on keyboard focus, not on the focus a click produces */
        var keyboard;
        try { keyboard = el.matches(":focus-visible"); }
        catch (e) { keyboard = performance.now() - scLastPointer > 400; }
        if (keyboard) scRun(el, node);
      });
      el.addEventListener("blur", function () {
        var a = scRunning.get(el);
        if (a) a.cancel();
      });
      el.addEventListener("pointerdown", function () {
        var a = scRunning.get(el);
        if (a) a.cancel();
      });
    });
  }

  document.addEventListener("pointerdown", function () { scLastPointer = performance.now(); }, true);

  window.CPScramble = {
    bind: scBind,
    run: function (host, node) { scRun(host, node || scLabelFor(host)); },
    config: { charset: SC_CHARS, duration: SC_MS, lockMin: SC_LOCK_MIN, lockSpan: SC_LOCK_SPAN, selector: SC_BIND },
    /* introspection for the verification harness and for anyone debugging
       a runaway loop: a settled page must report active 0 */
    stats: function () { return { active: scActive, maxActive: scMaxActive }; },
    labelFor: scLabelFor
  };

  /* ---------------------------------------------------------------
     Re-arm when dashboard.js injects content later.
     --------------------------------------------------------------- */
  function observeInjection() {
    var targets = ["#cards", "#alerts tbody", "#incidents-host"];
    var pending = false;
    function schedule() {
      if (pending) return;
      pending = true;
      setTimeout(function () {
        pending = false;
        indexAll();
        runCountUps();
        scBind(document);
      }, 40);
    }
    targets.forEach(function (sel) {
      var el = $(sel);
      if (!el) return;
      new MutationObserver(schedule).observe(el, { childList: true, subtree: true });
    });
  }

  function sequencePanels() {
    $$(".dash .panel").forEach(function (panel, i) {
      if (skip(panel, "M1")) return;
      panel.style.setProperty("--panel-delay", String(i * 150) + "ms");
      panel.classList.add("seq-panel");
    });
  }

  function boot() {
    sequencePanels();
    indexAll();
    runCountUps();
    setupPaint();
    bindScroll();
    watchDecode();
    watchPanels();
    watchPulse();
    setupRail();
    scBind(document);
    observeInjection();
    window.__motionReady = true;
  }

  var booted = false;
  function ready() {
    if (booted) return;
    if (root.classList.contains("entrance-pending") && !root.classList.contains("entrance-revealing")) return;
    booted = true;
    boot();
  }
  window.addEventListener("cam:entrance-reveal", ready);
  window.addEventListener("cam:entrance-complete", ready);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready);
  else ready();
})();
