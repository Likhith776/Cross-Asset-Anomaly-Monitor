/* Landing page data binding.
   Reads the published JSON snapshots that the CI pipeline writes next to the
   page and fills the hero status line, the tracked-market arc carousel, the
   latest-signal feature block and the recent-incidents grid.
   Every fetch is optional: with no data the page still renders, showing an
   explicit empty state instead of invented numbers. */
(function () {
  "use strict";

  var REPO = "https://github.com/Likhith776/Cross-Asset-Anomaly-Monitor";

  var cache = {};
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  function loadJSON(url) {
    if (!cache[url]) {
      cache[url] = fetch(url, { cache: "no-store" })
        .then(function (r) { if (!r.ok) throw new Error(r.status + " " + r.statusText); return r.json(); })
        .catch(function () { return null; });
    }
    return cache[url];
  }

  function symbolFile(s) { return s.replace(/\^/g, "idx_").replace(/=/g, "_"); }
  function fmt(v, d) {
    if (v === null || v === undefined || isNaN(v)) return "-";
    d = d === undefined ? 2 : d;
    return Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  }
  function utc(iso) {
    try { return new Date(iso).toUTCString().replace("GMT", "UTC"); } catch (e) { return "-"; }
  }
  function ageClass(s) { return s < 120 ? "live" : s < 900 ? "delayed" : "stale"; }
  function ageLabel(s) {
    if (s === null || s === undefined) return "unknown";
    return s < 120 ? "live" : s < 900 ? Math.round(s / 60) + "m old" : Math.round(s / 3600) + "h old";
  }
  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  /* ---- procedural cover artwork: a deterministic monochrome pattern ---- */
  function seeded(seed) {
    var h = 2166136261;
    for (var i = 0; i < seed.length; i++) { h ^= seed.charCodeAt(i); h = Math.imul(h, 16777619); }
    return function () {
      h ^= h << 13; h ^= h >>> 17; h ^= h << 5;
      return ((h >>> 0) % 100000) / 100000;
    };
  }

  function patternSVG(seed) {
    var rnd = seeded(seed);
    var W = 224, H = 335;
    var out = [];
    out.push('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '" role="img" aria-label="Generated monochrome pattern for ' + esc(seed) + '">');
    out.push('<rect width="' + W + '" height="' + H + '" fill="#000"/>');
    // ordered-dither field, drawn as squares on a grid so it reads as the same
    // visual family as the hero
    var cell = 8;
    for (var y = 0; y < H; y += cell) {
      for (var x = 0; x < W; x += cell) {
        var v = rnd();
        var cx = (x + cell / 2 - W * 0.3) / (W * 0.7);
        var cy = (y + cell / 2 - H * 0.38) / (H * 0.6);
        var fall = 1 - Math.min(1, Math.sqrt(cx * cx + cy * cy));
        var lum = Math.max(0, Math.min(1, v * 0.55 + fall * 0.75 - 0.12));
        var levels = [0, 27, 48, 71, 94, 119, 145, 171, 198, 226, 255];
        var q = levels[Math.min(levels.length - 1, Math.round(lum * (levels.length - 1)))];
        if (q === 0) continue;
        out.push('<rect x="' + x + '" y="' + y + '" width="' + cell + '" height="' + cell + '" fill="rgb(' + q + ',' + q + ',' + q + ')" opacity="' + (0.35 + lum * 0.65).toFixed(2) + '"/>');
      }
    }
    out.push('<rect x="0.5" y="0.5" width="' + (W - 1) + '" height="' + (H - 1) + '" fill="none" stroke="#575757"/>');
    out.push('<text x="14" y="' + (H - 18) + '" fill="#ffffff" font-family="JetBrains Mono, monospace" font-size="13" font-weight="700">' + esc(seed) + "</text>");
    out.push("</svg>");
    return out.join("");
  }

  function sparklineSVG(points, label) {
    var W = 224, H = 335;
    if (!points || points.length < 2) return patternSVG(label);
    var vals = points.map(function (p) { return p[1]; });
    var min = Math.min.apply(null, vals);
    var max = Math.max.apply(null, vals);
    var span = max - min || 1;
    var pad = 18;
    var path = points.map(function (p, i) {
      var x = pad + (i / (points.length - 1)) * (W - pad * 2);
      var y = pad + (1 - (p[1] - min) / span) * (H - pad * 2 - 34);
      return (i ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
    }).join(" ");
    var area = path + " L" + (W - pad) + " " + (H - pad) + " L" + pad + " " + (H - pad) + " Z";
    return [
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '" role="img" aria-label="' + esc(label) + ' price history">',
      '<rect width="' + W + '" height="' + H + '" fill="#000"/>',
      '<path d="' + area + '" fill="rgba(255,255,255,.07)"/>',
      '<path d="' + path + '" fill="none" stroke="#ffffff" stroke-width="1.2"/>',
      '<text x="14" y="' + (H - 30) + '" fill="#8F8F8F" font-family="JetBrains Mono, monospace" font-size="11">' + esc(label) + "</text>",
      '<text x="14" y="' + (H - 14) + '" fill="#ffffff" font-family="JetBrains Mono, monospace" font-size="13" font-weight="700">' + fmt(points[points.length - 1][1], max > 1000 ? 0 : 2) + "</text>",
      "</svg>"
    ].join("");
  }

  /* ---- sections ---- */
  function renderHeroStatus(latest, anomalies) {
    var host = document.querySelector("[data-hero-status]");
    if (!host) return;
    if (!latest) {
      host.textContent = "awaiting first published snapshot";
      return;
    }
    var n = Object.keys(latest.symbols || {}).length;
    var anoms = anomalies && anomalies.events ? anomalies.events.length : 0;
    host.innerHTML =
      '<span class="chip chip--accent">live</span>' +
      '<span>' + n + " instruments</span>" +
      "<span>" + anoms + " anomalies recorded</span>" +
      "<span>published " + esc(utc(latest.generated_at)) + "</span>";
  }

  function cardHTML(sym, q, artwork) {
    var r1 = q.return_1m;
    var deltaCls = r1 === null || r1 === undefined ? "flat" : r1 > 0 ? "up" : r1 < 0 ? "down" : "flat";
    var deltaTxt = r1 === null || r1 === undefined ? "n/a" : (r1 >= 0 ? "+" : "") + (r1 * 100).toFixed(2) + "% 1m";
    var z = q.zscore === null || q.zscore === undefined ? "-" : fmt(q.zscore, 2);
    return [
      '<article class="card">',
      '<div class="card__tags">',
      '<span class="card__tag">' + esc(sym) + "</span>",
      '<span class="card__tag">z ' + z + "</span>",
      "</div>",
      '<div class="card__cover-wrap">',
      '<a class="card__cover" href="dashboard.html#' + esc(symbolFile(sym)) + '" aria-label="Open ' + esc(q.label || sym) + ' on the dashboard">',
      '<span class="card__cover-surface">' + artwork + "</span>",
      "</a>",
      "</div>",
      '<div class="card__text">',
      '<h3 class="card__title"><a href="dashboard.html#' + esc(symbolFile(sym)) + '">' + esc(q.label || sym) + "</a></h3>",
      '<p class="card__subtitle">',
      '<span class="card__value">' + fmt(q.price, Math.abs(q.price) > 1000 ? 0 : 2) + "</span>",
      '<span class="card__delta card__delta--' + deltaCls + '">' + deltaTxt + "</span>",
      '<span class="' + ageClass(q.age_seconds || 0) + '">' + esc(ageLabel(q.age_seconds)) + "</span>",
      "</p>",
      '<a class="card__link" href="dashboard.html#' + esc(symbolFile(sym)) + '">Open on dashboard</a>',
      "</div>",
      "</article>"
    ].join("");
  }

  function renderArc(latest, charts) {
    var ring = document.querySelector("[data-arc-ring]");
    var carousel = document.querySelector("[data-arc-carousel]");
    if (!ring) return;
    if (!latest || !latest.symbols) {
      carousel && carousel.classList.add("is-ready");
      ring.innerHTML = '<div class="empty-state" style="width:min(560px,80vw);margin:0 auto">No market snapshot published yet. The tracking row fills in on the next pipeline run.</div>';
      return;
    }
    var order = Object.keys(latest.symbols);
    ring.innerHTML = order.map(function (sym) {
      var q = latest.symbols[sym];
      var ch = charts[symbolFile(sym)];
      var art = ch && ch.points ? sparklineSVG(ch.points, sym) : patternSVG(sym);
      return cardHTML(sym, q, art);
    }).join("");
    if (window.CPArcCarousel && carousel) window.CPArcCarousel.init(carousel);
  }

  function renderFeatured(latest, anomalies, charts) {
    var host = document.querySelector("[data-featured]");
    if (!host) return;
    var events = (anomalies && anomalies.events) || [];
    if (!events.length) {
      host.innerHTML = '<div class="empty-state">No anomalies recorded yet. The detector publishes to this block as soon as one fires.</div>';
      return;
    }
    var e = events.slice().sort(function (a, b) { return new Date(b.timestamp) - new Date(a.timestamp); })[0];
    var ch = charts[symbolFile(e.symbol)];
    var art = ch && ch.points ? sparklineSVG(ch.points, e.symbol) : patternSVG(e.symbol);
    host.innerHTML = [
      '<div class="featured">',
      '<div class="featured__surface">' + art + "</div>",
      '<div class="featured__caption">',
      '<div class="featured__meta">',
      '<span class="chip chip--sev-' + esc(e.severity || "low") + '">' + esc(e.severity || "low") + "</span>",
      "<span>" + esc(e.symbol) + "</span>",
      "<span>score " + fmt(e.score, 3) + "</span>",
      "<span>" + esc(utc(e.timestamp)) + "</span>",
      "</div>",
      '<div class="featured__title">' + esc(e.type || "anomaly") + " on " + esc(e.symbol) + "</div>",
      '<div class="featured__subtitle">' + esc(e.description || "") + "</div>",
      '<a class="featured__link" href="dashboard.html#anomalies">Read the alert on the dashboard</a>',
      "</div>",
      "</div>"
    ].join("");
  }

  function renderIncidents(incidents) {
    var host = document.querySelector("[data-incidents]");
    if (!host) return;
    var list = (incidents && incidents.incidents) || [];
    if (!list.length) {
      host.innerHTML = '<div class="empty-state">No incidents in the retention window.</div>';
      return;
    }
    host.innerHTML = list.slice(0, 4).map(function (e, i) {
      var sev = e.severity || "low";
      return [
        '<article class="record-card">',
        '<div class="record-card__image-wrap">',
        '<a class="record-card__image" href="dashboard.html#incidents" aria-label="Incident on ' + esc(e.symbol) + '">',
        '<span class="record-card__image-surface">' + patternSVG(e.symbol + e.timestamp) + "</span>",
        "</a>",
        "</div>",
        '<div class="record-card__text">',
        '<span class="record-card__index">' + String(i + 1).padStart(2, "0") + " &middot; " + esc(sev) + " &middot; " + esc(utc(e.timestamp)) + "</span>",
        '<h3 class="record-card__title">' + esc(e.symbol) + " &mdash; " + esc(e.type || "anomaly") + "</h3>",
        '<p class="record-card__excerpt">' + esc((e.description || "").slice(0, 180)) + "</p>",
        '<a class="record-card__link" href="dashboard.html#incidents">Open the incident log</a>',
        "</div>",
        "</article>"
      ].join("");
    }).join("");
  }

  function renderUpdated(latest) {
    // The timestamp appears in the lede meta and in the mobile menu overlay,
    // so every node carrying the hook is refreshed, not just the first.
    var txt = latest ? "last published " + utc(latest.generated_at) : "no snapshot published yet";
    $$("[data-updated]").forEach(function (el) { el.textContent = txt; });
    var year = $("[data-year]");
    if (year) year.textContent = String(new Date().getFullYear());
    var repo = $("[data-repo-link]");
    if (repo) repo.href = REPO;
  }

  function boot() {
    Promise.all([
      loadJSON("data/latest.json"),
      loadJSON("data/anomalies.json"),
      loadJSON("data/incidents.json")
    ]).then(function (res) {
      var latest = res[0], anomalies = res[1], incidents = res[2];
      renderUpdated(latest);
      renderHeroStatus(latest, anomalies);
      renderIncidents(incidents);
      renderFeatured(latest, anomalies, {});
      renderArc(latest, {});
      if (window.CPReveal) window.CPReveal.refresh();
      if (!latest) return;
      var order = Object.keys(latest.symbols || {});
      var charts = {};
      Promise.all(order.map(function (sym) {
        return loadJSON("data/charts/" + symbolFile(sym) + ".json").then(function (d) { if (d) charts[symbolFile(sym)] = d; });
      })).then(function () {
        renderArc(latest, charts);
        renderFeatured(latest, anomalies, charts);
        if (window.CPReveal) window.CPReveal.refresh();
      });
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
