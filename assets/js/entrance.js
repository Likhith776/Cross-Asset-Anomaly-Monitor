(function () {
  "use strict";

  /* Values are the reference entrance's own, read from its bundle
     (design-extraction/codapress/bundles/BaseLayout.*.js): 50x30 cell
     grids, black under white, 1s power4.out per cell, 1.2s diagonal
     stagger, 200ms black offset, splash hold 0-1s, grid inset wipe
     1-1.4s, content slide -5vw/-5% and header drop from 1.4s. */
  var COLS = 50;
  var ROWS = 30;
  var CELL_MS = 1000;
  var STAGGER_MS = 1200;
  var BLACK_OFFSET = 200;
  var SPLASH_MS = 1000;
  var WIPE_MS = 400;
  var REVEAL_AT = 1400;
  var END_MS = 3800;
  var FRAME = 10;

  var root = document.documentElement;
  var pending = root.classList.contains("entrance-pending");
  if (!pending) return;

  var layer = document.getElementById("dashboard-entrance");
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  var rafId = 0;
  var started = 0;
  var done = false;
  var context;
  var canvas;
  var width;
  var height;
  var cellW;
  var cellH;
  var scrollX = window.scrollX;
  var scrollY = window.scrollY;

  function finish(reason) {
    if (done) return;
    done = true;
    cancelAnimationFrame(rafId);
    clearTimeout(window.__entranceWatchdog);
    root.classList.remove("entrance-pending", "entrance-active", "entrance-revealing");
    if (layer) layer.remove();
    document.removeEventListener("keydown", onKey, true);
    window.removeEventListener("pagehide", onHide);
    window.removeEventListener("resize", onResize);
    reduce.removeEventListener("change", onReduce);
    window.scrollTo(scrollX, scrollY);
    window.__dashboardEntrance = { state: "complete", reason: reason };
    try { sessionStorage.setItem("cam_dashboard_entrance", "1"); } catch (e) {}
    window.dispatchEvent(new CustomEvent("cam:entrance-complete"));
  }

  function onKey(event) {
    if (event.key === "Escape" || event.key === "Tab") finish("keyboard");
  }
  function onHide() { finish("pagehide"); }
  function onReduce() { if (reduce.matches) finish("reduced-motion"); }
  function onResize() { finish("resize"); }

  if (!layer || reduce.matches || root.getAttribute("data-motion") === "off") {
    finish("disabled");
    return;
  }

  document.addEventListener("keydown", onKey, true);
  window.addEventListener("pagehide", onHide);
  window.addEventListener("resize", onResize);
  reduce.addEventListener("change", onReduce);
  layer.querySelector("button").addEventListener("click", function () { finish("skip"); });

  function easeInOut3(t) {
    t = Math.max(0, Math.min(1, t));
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  }
  function easeOut4(t) {
    t = Math.max(0, Math.min(1, t));
    return 1 - Math.pow(1 - t, 4);
  }

  function drawTiles(elapsed) {
    var layers = [
      { color: "#000000", offset: BLACK_OFFSET },
      { color: "#ffffff", offset: 0 }
    ];
    for (var l = 0; l < layers.length; l++) {
      var layer2 = layers[l];
      context.fillStyle = layer2.color;
      for (var r = 0; r < ROWS; r++) {
        for (var c = 0; c < COLS; c++) {
          var p = easeOut4((elapsed - layer2.offset - ((c + r) / (COLS + ROWS - 2)) * STAGGER_MS) / CELL_MS);
          var s = 1.05 * (1 - p);
          if (s < 0.001) continue;
          var cx = (c + 0.5) * cellW;
          var cy = (r + 0.5) * cellH;
          var w = cellW * s;
          var h = cellH * s;
          context.fillRect(cx - w / 2, cy - h / 2, w + 0.5, h + 0.5);
        }
      }
    }
  }

  function draw(now) {
    if (done) return;
    var elapsed = now - started;
    context.clearRect(0, 0, width, height);
    if (elapsed < REVEAL_AT) {
      /* white splash; the grid inset wipes 0 -> 10px across 1-1.4s */
      var inset = FRAME * easeInOut3((elapsed - SPLASH_MS) / WIPE_MS);
      context.fillStyle = "#ffffff";
      context.fillRect(0, 0, width, height);
      context.save();
      context.beginPath();
      context.rect(inset, inset, width - inset * 2, height - inset * 2);
      context.clip();
      context.fillStyle = "#000000";
      context.fillRect(0, 0, width, height);
      context.fillStyle = "#ffffff";
      context.fillRect(0, 0, width, height);
      context.restore();
    } else {
      if (!root.classList.contains("entrance-revealing")) {
        root.classList.add("entrance-revealing");
        window.dispatchEvent(new CustomEvent("cam:entrance-reveal"));
      }
      drawTiles(elapsed - REVEAL_AT);
    }
    if (elapsed >= END_MS) { finish("complete"); return; }
    rafId = requestAnimationFrame(draw);
  }

  function start() {
    if (done || !root.classList.contains("entrance-pending")) return;
    try {
      canvas = layer.querySelector("canvas");
      width = document.documentElement.clientWidth;
      height = window.innerHeight;
      var ratio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      context = canvas.getContext("2d", { alpha: true });
      if (!context) { finish("canvas-unavailable"); return; }
      context.scale(ratio, ratio);
      cellW = width / COLS;
      cellH = height / ROWS;
      started = performance.now();
      window.__dashboardEntrance = { state: "revealing", startedAt: started };
      root.classList.add("entrance-active");
      draw(started);
    } catch (e) { finish("render-error"); }
  }

  window.__dashboardEntrance = { state: "waiting" };
  // The intro is bounded independently of data/CDN readiness; it is not a download meter.
  var fonts = document.fonts ? document.fonts.ready.catch(function () {}) : Promise.resolve();
  Promise.all([
    new Promise(function (resolve) { setTimeout(resolve, 420); }),
    Promise.race([fonts, new Promise(function (resolve) { setTimeout(resolve, 900); })])
  ]).then(start).catch(function () { finish("boot-error"); });
  window.addEventListener("cam:entrance-timeout", function () { finish("timeout"); }, { once: true });
})();
