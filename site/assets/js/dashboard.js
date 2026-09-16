/* Dashboard logic.
   Same data contracts as before (data/latest.json, data/anomalies.json,
   data/correlations.json, data/charts/<symbol>.json, data/feedback.json,
   data/precision.json, data/incidents.json), same detectors, filters and
   drill-downs; the presentation layer is the new design system.
   Every panel resolves to one of three explicit states - ready, empty or
   error - so a failed fetch is visible rather than blank. */
(function () {
  "use strict";

  var SEV_ORDER = { critical: 3, high: 2, medium: 1, low: 0 };
  var POLL_MS = 300000;
  var STALE_AFTER = 900;

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function fmt(v, d) {
    if (v === null || v === undefined || isNaN(v)) return "-";
    d = d === undefined ? 2 : d;
    return Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  }
  function utc(iso) {
    try { return new Date(iso).toUTCString().replace("GMT", "UTC"); } catch (e) { return "-"; }
  }
  function isoShort(iso) {
    try { return new Date(iso).toISOString().replace("T", " ").slice(0, 19); } catch (e) { return "-"; }
  }
  function ageCls(s) { return s < 120 ? "live" : s < 900 ? "delayed" : "stale"; }
  function ageLbl(s) {
    if (s === null || s === undefined) return "age unknown";
    return s < 120 ? "live" : s < 900 ? Math.round(s / 60) + "m old" : Math.round(s / 3600) + "h old";
  }
  function sevPasses(sev, minSev) { return SEV_ORDER[sev] >= SEV_ORDER[minSev]; }
  function symbolFile(s) { return s.replace(/\^/g, "idx_").replace(/=/g, "_"); }

  async function j(url) {
    var r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error(r.status + " " + r.statusText + " - " + url);
    return r.json();
  }

  function setStatus(kind, text) {
    var el = $("[data-status]");
    if (!el) return;
    el.className = "cp-status cp-status--" + kind;
    el.textContent = text;
    el.hidden = !text;
  }

  function chartState(name, kind, text) {
    var host = $('[data-chart-state="' + name + '"]');
    if (!host) return;
    host.textContent = text || "";
    host.classList.toggle("is-visible", !!text);
    host.classList.toggle("empty-state--error", kind === "error");
  }

  /* ECharts theme - the reference palette, monochrome with one accent. */
  var AXIS = {
    axisLine: { lineStyle: { color: "#575757" } },
    axisTick: { lineStyle: { color: "#575757" } },
    axisLabel: { color: "#8F8F8F", fontFamily: "'JetBrains Mono', monospace", fontSize: 11 },
    splitLine: { lineStyle: { color: "#1c1c1c" } }
  };
  function baseOption() {
    return {
      backgroundColor: "transparent",
      textStyle: { fontFamily: "'JetBrains Mono', monospace", color: "#8F8F8F" },
      tooltip: {
        backgroundColor: "#0a0a0a",
        borderColor: "#575757",
        textStyle: { color: "#ffffff", fontFamily: "'JetBrains Mono', monospace", fontSize: 12 }
      }
    };
  }
  function applyAxisTheme(opt) {
    ["xAxis", "yAxis"].forEach(function (key) {
      var ax = opt[key];
      if (!ax) return;
      (Array.isArray(ax) ? ax : [ax]).forEach(function (a) {
        Object.keys(AXIS).forEach(function (k) {
          if (k === "splitLine" && (a.type === "category" || a.splitLine === false)) return;
          a[k] = Object.assign({}, AXIS[k], a[k] || {});
        });
      });
    });
    return opt;
  }

  var DETECTOR_KEYWORDS = {
    zscore_spike: "zscore",
    isolation_forest_outlier: "iforest",
    correlation_break: "correlation",
    joint_mahalanobis: "joint"
  };
  function detectorFromDescription(desc) {
    if (!desc) return "unknown";
    for (var k in DETECTOR_KEYWORDS) {
      if (desc.indexOf(k) !== -1) return DETECTOR_KEYWORDS[k];
    }
    return "unknown";
  }
  function sevOf(e) { return e.severity || "low"; }

  var state = {
    latest: null,
    anomalies: { events: [] },
    feedback: {},
    selected: null,
    priceChart: null,
    corrChart: null,
    timelineChart: null,
    currentSymbol: null
  };

  /* ---------- header / hero ---------- */
  function renderMeta(latest) {
    var txt;
    if (!latest) {
      txt = "no snapshot available";
      setStatus("error", "No live snapshot could be loaded - the panels below show their empty state.");
    } else {
      txt = "last published " + utc(latest.generated_at);
    }
    // Both the hero row and the mobile menu overlay carry these hooks.
    $$("[data-updated]").forEach(function (el) { el.textContent = txt; });
    $$("[data-hero-ts]").forEach(function (el) { el.textContent = txt; });
    if (!latest) return;
    var ages = Object.keys(latest.symbols || {}).map(function (s) { return latest.symbols[s].age_seconds || 0; });
    var worst = ages.length ? Math.max.apply(null, ages) : 0;
    if (worst > STALE_AFTER) {
      setStatus("stale", "Data is stale: the freshest snapshot is " + ageLbl(worst) + ". The publisher runs every 30 minutes.");
    } else {
      setStatus("live", "Live - publishing on a 30-minute CI cycle, no server involved.");
    }
  }

  /* ---------- asset status ---------- */
  function renderCards(latest) {
    var host = $("#cards");
    if (!host) return;
    if (!latest || !latest.symbols) {
      host.innerHTML = '<div class="empty-state">No market snapshot published yet.</div>';
      return;
    }
    host.innerHTML = Object.keys(latest.symbols).map(function (sym) {
      var q = latest.symbols[sym];
      var r1 = q.return_1m;
      var deltaCls = r1 === null || r1 === undefined ? "pct--down" : r1 >= 0 ? "pct--up" : "pct--down";
      var deltaTxt = r1 === null || r1 === undefined ? "n/a" : (r1 >= 0 ? "+" : "") + (r1 * 100).toFixed(2) + "% 1m";
      var regime = q.regime && q.regime !== "unknown"
        ? '<span class="chip chip--regime-' + esc(q.regime) + '">' + esc(q.regime) + "</span>" : "";
      return [
        '<button type="button" class="asset-card" data-sym="' + esc(sym) + '" data-file="' + esc(symbolFile(sym)) + '" aria-pressed="false">',
        '<span class="asset-card__label">' + esc(q.label || sym) + "</span>",
        '<span class="asset-card__price">' + fmt(q.price, Math.abs(q.price) > 1000 ? 0 : 2) + "</span>",
        '<span class="asset-card__meta">',
        '<span class="dot dot--' + ageCls(q.age_seconds || 0) + '"></span>' + esc(ageLbl(q.age_seconds)),
        '<span class="' + deltaCls + '">' + deltaTxt + "</span>",
        "<span>" + esc(q.source || "") + "</span>",
        "<span>z " + (q.zscore === null || q.zscore === undefined ? "-" : fmt(q.zscore, 2)) + "</span>",
        regime,
        "</span>",
        "</button>"
      ].join("");
    }).join("");

    $$(".asset-card", host).forEach(function (card) {
      card.addEventListener("click", function () {
        loadPriceChart(card.dataset.file, card.dataset.sym);
        var el = document.getElementById("assets");
        if (el && el.scrollIntoView) el.scrollIntoView({ block: "start" });
      });
    });
  }

  /* ---------- price history ---------- */
  function nearestIndex(points, iso) {
    var t = new Date(iso).getTime();
    var best = null, bestDiff = Infinity;
    for (var i = 0; i < points.length; i++) {
      var diff = Math.abs(new Date(points[i][0]).getTime() - t);
      if (diff < bestDiff) { bestDiff = diff; best = i; }
    }
    return bestDiff < 45 * 60 * 1000 ? best : null;
  }

  async function loadPriceChart(file, sym) {
    var el = document.getElementById("price-chart");
    if (!el || !window.echarts) return;
    state.currentSymbol = sym || null;
    $$("#symbol-bar button").forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.dataset.file === file));
    });
    chartState("price", "loading", "loading price history...");
    var data;
    try {
      data = await j("data/charts/" + file + ".json");
    } catch (err) {
      chartState("price", "error", "Price history unavailable - " + err.message);
      return;
    }
    chartState("price", "ready", "");
    if (!data || !data.points || !data.points.length) {
      chartState("price", "empty", "No price points recorded for this instrument yet.");
      return;
    }
    state.priceChart = state.priceChart || echarts.init(el, null, { renderer: "canvas" });
    var marks = state.anomalies.events
      .filter(function (e) { return symbolFile(e.symbol) === file; })
      .map(function (e) {
        var i = nearestIndex(data.points, e.timestamp);
        if (i === null) return null;
        var sev = sevOf(e);
        return {
          coord: data.points[i],
          value: sev.charAt(0).toUpperCase(),
          itemStyle: { color: sev === "critical" ? "#ffffff" : sev === "high" ? "#c9c9c9" : sev === "medium" ? "#8F8F8F" : "#575757" }
        };
      }).filter(Boolean);

    state.priceChart.setOption(applyAxisTheme(Object.assign(baseOption(), {
      grid: { left: 64, right: 20, top: 24, bottom: 40 },
      xAxis: { type: "time" },
      yAxis: { type: "value", scale: true, axisLabel: { formatter: function (v) { return fmt(v, Math.abs(v) > 1000 ? 0 : 2); } } },
      series: [{
        name: data.symbol || file,
        type: "line",
        showSymbol: false,
        lineStyle: { color: "#ffffff", width: 1.2 },
        areaStyle: { color: "rgba(255,255,255,.06)" },
        data: data.points,
        markPoint: { symbolSize: 22, data: marks }
      }]
    })), { notMerge: true });
  }

  /* ---------- correlations ---------- */
  async function loadCorrelation() {
    var el = document.getElementById("corr-heatmap");
    var summary = $("#corr-summary");
    if (!el || !window.echarts) return;
    chartState("corr", "loading", "loading correlation structure...");
    var data;
    try {
      data = await j("data/correlations.json");
    } catch (err) {
      chartState("corr", "error", "Correlation snapshot unavailable - " + err.message);
      if (summary) summary.textContent = "";
      return;
    }
    chartState("corr", "ready", "");
    var syms = data.symbols || [];
    var cells = [];
    for (var i = 0; i < syms.length; i++) {
      for (var k = 0; k < syms.length; k++) {
        var v = data.matrix[i] ? data.matrix[i][k] : null;
        if (v !== null && v !== undefined) cells.push([k, i, v]);
      }
    }
    var lastSnap = data.history && data.history.length ? data.history[data.history.length - 1] : null;
    if (summary) {
      summary.textContent = lastSnap
        ? "Last snapshot: " + utc(lastSnap.timestamp)
        : "No correlation snapshots yet - the first pipeline run seeds them.";
    }
    if (!cells.length) {
      chartState("corr", "empty", "No correlation matrix published yet.");
      return;
    }
    state.corrChart = state.corrChart || echarts.init(el, null, { renderer: "canvas" });
    state.corrChart.setOption(applyAxisTheme(Object.assign(baseOption(), {
      tooltip: Object.assign(baseOption().tooltip, {
        position: "top",
        formatter: function (p) { return syms[p.value[1]] + " / " + syms[p.value[0]] + "<br><b>" + p.value[2].toFixed(3) + "</b>"; }
      }),
      grid: { left: 96, right: 16, top: 16, bottom: 96 },
      xAxis: { type: "category", data: syms, axisLabel: { rotate: 45, fontSize: 11 } },
      yAxis: { type: "category", data: syms, axisLabel: { fontSize: 11 } },
      visualMap: {
        min: -1, max: 1, calculable: true, orient: "horizontal", left: "center", bottom: 0,
        textStyle: { color: "#8F8F8F", fontFamily: "'JetBrains Mono', monospace", fontSize: 11 },
        inRange: { color: ["#3a3a3a", "#7d7d7d", "#ffffff"] },
        text: ["+1", "-1"]
      },
      series: [{
        name: "Pearson r",
        type: "heatmap",
        data: cells,
        label: { show: true, fontSize: 9, color: "#000000", formatter: function (p) { return p.value[2].toFixed(2); } },
        emphasis: { itemStyle: { borderColor: "#ffffff", borderWidth: 1 } }
      }]
    })), { notMerge: true });
  }

  /* ---------- anomaly timeline ---------- */
  function filteredEvents() {
    var win = $("#t-window").value;
    var minSev = $("#t-severity").value;
    var minScore = parseFloat($("#t-score").value) / 100;
    var cutoff = win === "all" ? null : Date.now() - parseInt(win, 10) * 60000;
    return state.anomalies.events.filter(function (e) {
      if (cutoff && new Date(e.timestamp).getTime() < cutoff) return false;
      if (minSev !== "all" && !sevPasses(sevOf(e), minSev)) return false;
      if (e.score < minScore) return false;
      return true;
    });
  }

  function renderTimeline() {
    var el = document.getElementById("timeline-chart");
    if (!el || !window.echarts) return;
    var filtered = filteredEvents();
    var count = $("#t-count");
    if (count) count.textContent = filtered.length + " of " + state.anomalies.events.length + " events";
    if (!state.anomalies.events.length) {
      chartState("timeline", "empty", "No anomalies recorded yet.");
      return;
    }
    chartState("timeline", "ready", "");
    var bySeverity = { low: [], medium: [], high: [], critical: [] };
    filtered.forEach(function (e) {
      (bySeverity[sevOf(e)] || bySeverity.medium).push([e.timestamp, e.score, e.symbol]);
    });
    var colors = { low: "#575757", medium: "#8F8F8F", high: "#c9c9c9", critical: "#ffffff" };
    var series = Object.keys(bySeverity).filter(function (s) { return bySeverity[s].length; }).map(function (sev) {
      return {
        name: sev, type: "scatter",
        symbolSize: function (d) { return 7 + d[1] * 20; },
        data: bySeverity[sev],
        itemStyle: { color: colors[sev] }
      };
    });
    state.timelineChart = state.timelineChart || echarts.init(el, null, { renderer: "canvas" });
    state.timelineChart.setOption(applyAxisTheme(Object.assign(baseOption(), {
      tooltip: Object.assign(baseOption().tooltip, {
        trigger: "item",
        formatter: function (p) {
          return p.value[2] + "<br>score=" + p.value[1].toFixed(3) + "<br>" + utc(p.value[0]);
        }
      }),
      legend: { data: Object.keys(bySeverity).filter(function (s) { return bySeverity[s].length; }), textStyle: { color: "#8F8F8F", fontFamily: "'JetBrains Mono', monospace" } },
      grid: { left: 64, right: 20, top: 44, bottom: 40 },
      xAxis: { type: "time" },
      yAxis: { type: "value", name: "score", min: 0, max: 1 },
      series: series
    })), { notMerge: true });
  }

  /* ---------- alerts table ---------- */
  function alertDetailHTML(e) {
    var chips = [
      e.type ? '<span class="chip">' + esc(e.type) + "</span>" : "",
      e.detector ? '<span class="chip">' + esc(e.detector) + "</span>" : "",
      '<span class="chip chip--sev-' + esc(sevOf(e)) + '">' + esc(sevOf(e)) + "</span>",
      e.regime ? '<span class="chip chip--regime-' + esc(e.regime) + '">regime: ' + esc(e.regime) + "</span>" : "",
      e.macro_context ? '<span class="chip">macro: ' + esc(e.macro_context) + "</span>" : "",
      e.lead_lag ? '<span class="chip">led by ' + esc(e.lead_lag.leader) + " (" + esc(e.lead_lag.lag_ticks) + "t, r=" + esc(e.lead_lag.correlation) + ")</span>" : ""
    ].filter(Boolean).join("");
    var llm = e.llm_explanation ? '<div class="alert-detail__llm">' + esc(e.llm_explanation) + "</div>" : "";
    return '<div class="alert-detail__chips">' + chips + "</div>" +
      '<div class="alert-detail__body">' + esc(e.description || "") + "</div>" + llm;
  }

  function renderAlerts() {
    var minSev = $("#r-severity").value;
    var minScore = parseFloat($("#r-score").value) / 100;
    var sortKey = $("#r-sort").value;
    var filtered = state.anomalies.events.filter(function (e) {
      if (minSev !== "all" && !sevPasses(sevOf(e), minSev)) return false;
      if (e.score < minScore) return false;
      return true;
    });
    if (sortKey === "score") {
      filtered = filtered.slice().sort(function (a, b) {
        return SEV_ORDER[sevOf(a)] - SEV_ORDER[sevOf(b)] || b.score - a.score;
      });
    } else {
      filtered = filtered.slice().sort(function (a, b) { return new Date(b.timestamp) - new Date(a.timestamp); });
    }
    var count = $("#r-count");
    if (count) count.textContent = filtered.length + " of " + state.anomalies.events.length + " events";

    var tbody = $("#alerts tbody");
    if (!tbody) return;
    if (!filtered.length) {
      tbody.innerHTML = '<tr><td colspan="7"><div class="empty-state">' +
        (state.anomalies.events.length ? "No alerts match the current filters." : "No anomalies have been detected yet.") +
        "</div></td></tr>";
      return;
    }
    var rows = filtered.slice(0, 200);
    tbody.innerHTML = rows.map(function (e, idx) {
      var label = state.feedback[e.symbol + "|" + e.timestamp];
      var labelCell = label
        ? '<span class="chip' + (label === "confirmed" ? ' chip--solid' : "") + '">' + (label === "confirmed" ? "confirmed" : "false positive") + "</span>"
        : '<span style="color:var(--color-muted)">-</span>';
      return [
        '<tr data-selectable data-row="' + idx + '">',
        "<td>" + esc(isoShort(e.timestamp)) + "</td>",
        "<td>" + esc(e.symbol) + "</td>",
        "<td>" + esc(detectorFromDescription(e.description)) + "</td>",
        '<td><span class="chip chip--sev-' + esc(sevOf(e)) + '">' + esc(sevOf(e)) + "</span></td>",
        "<td>" + fmt(e.score, 3) + "</td>",
        "<td>" + labelCell + "</td>",
        "<td>" + esc((e.description || "").slice(0, 90)) + "</td>",
        "</tr>"
      ].join("");
    }).join("");

    function selectRow(idx) {
      var e = rows[idx];
      var panel = $("#alert-detail");
      if (!panel) return;
      if (state.selected === e) {
        panel.innerHTML = "";
        state.selected = null;
        $$("#alerts tbody tr").forEach(function (tr) { tr.removeAttribute("aria-selected"); });
        return;
      }
      state.selected = e;
      panel.innerHTML = alertDetailHTML(e);
      $$("#alerts tbody tr").forEach(function (tr) {
        tr.setAttribute("aria-selected", String(tr.dataset.row === String(idx)));
      });
    }

    $$("#alerts tbody tr[data-selectable]").forEach(function (tr) {
      var idx = parseInt(tr.dataset.row, 10);
      tr.addEventListener("click", function () { selectRow(idx); });
      tr.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); selectRow(idx); }
      });
      tr.tabIndex = 0;
    });
  }

  async function loadFeedback() {
    try {
      var fb = await j("data/feedback.json");
      state.feedback = {};
      (fb.feedback || []).forEach(function (f) { state.feedback[f.symbol + "|" + f.timestamp] = f.label; });
    } catch (err) {
      state.feedback = {};
    }
  }

  /* ---------- detector performance ---------- */
  async function loadPerformance() {
    var chip = $("#precision-chip");
    var table = $("#precision-table");
    if (!chip || !table) return;
    var p;
    try {
      p = await j("data/precision.json");
    } catch (err) {
      chip.textContent = "Precision metrics unavailable - " + err.message;
      table.innerHTML = "";
      return;
    }
    if (!p || !p.total_labeled) {
      chip.textContent = "No human labels yet - precision appears once alerts are reviewed.";
      table.innerHTML = "";
      return;
    }
    var overall = p.overall_precision === null || p.overall_precision === undefined
      ? "-" : (p.overall_precision * 100).toFixed(0) + "%";
    var parts = ["<b>Rolling precision (" + esc(p.window_days) + "d):</b> " + overall + " on " + esc(p.total_labeled) + " labels"];
    (p.by_detector || []).forEach(function (d) {
      if (d.labeled) parts.push(esc(d.detector) + ": " + (d.precision * 100).toFixed(0) + "% (" + d.confirmed + "/" + d.labeled + ")");
    });
    chip.innerHTML = parts.join(" &middot; ");
    var rows = (p.by_detector || []).map(function (d) {
      return "<tr><td>" + esc(d.detector) + "</td><td>" + d.labeled + "</td><td>" + d.confirmed +
        "</td><td>" + d.false_positive + "</td><td>" +
        (d.precision === null || d.precision === undefined ? "-" : (d.precision * 100).toFixed(0) + "%") +
        "</td></tr>";
    }).join("");
    table.innerHTML = '<table class="data-table"><caption class="visually-hidden">Per-detector precision over the rolling window</caption>' +
      "<thead><tr><th>Detector</th><th>Labeled</th><th>Confirmed</th><th>False positives</th><th>Precision</th></tr></thead>" +
      "<tbody>" + rows + "</tbody></table>";
  }

  /* ---------- incident log ---------- */
  var INCIDENT_PAGE = 25;

  function incidentHTML(e) {
    var sev = e.severity || "low";
    var chips = [
      '<span class="chip chip--sev-' + esc(sev) + '">' + esc(sev) + "</span>",
      e.type ? '<span class="chip">' + esc(e.type) + "</span>" : "",
      e.detector ? '<span class="chip">' + esc(e.detector) + "</span>" : "",
      e.regime && e.regime !== "unknown" ? '<span class="chip chip--regime-' + esc(e.regime) + '">regime: ' + esc(e.regime) + "</span>" : "",
      e.macro_context ? '<span class="chip">macro: ' + esc(e.macro_context) + "</span>" : "",
      e.lead_lag ? '<span class="chip">led by ' + esc(e.lead_lag.leader) + " (" + esc(e.lead_lag.lag_ticks) + "t, r=" + esc(e.lead_lag.correlation) + ")</span>" : ""
    ].filter(Boolean).join("");
    var llm = e.llm_explanation ? '<div class="incident__llm">' + esc(e.llm_explanation) + "</div>" : "";
    return [
      '<article class="incident incident--' + esc(sev) + '">',
      '<div class="incident__head">',
      '<span class="incident__symbol">' + esc(e.symbol) + "</span>",
      '<span class="incident__score">' + fmt(e.score, 3) + "</span>",
      '<span class="incident__time">' + esc(isoShort(e.timestamp)) + " UTC</span>",
      '<span class="incident__chips">' + chips + "</span>",
      "</div>",
      '<div class="incident__body">' + esc(e.description || "") + "</div>",
      llm,
      '<div class="incident__actions"><a class="link-underline" href="#assets" data-jump="' + esc(symbolFile(e.symbol)) + '" data-symbol="' + esc(e.symbol) + '">View price chart</a></div>',
      "</article>"
    ].join("");
  }

  function bindJumpLinks(host) {
    $$("[data-jump]", host).forEach(function (a) {
      a.addEventListener("click", function (ev) {
        ev.preventDefault();
        loadPriceChart(a.dataset.jump, a.dataset.symbol);
        var el = document.getElementById("assets");
        if (el && el.scrollIntoView) el.scrollIntoView({ block: "start" });
      });
    });
  }

  async function loadIncidents() {
    var note = $("#incidents-note");
    var host = $("#incidents-host");
    if (!note || !host) return;
    var data;
    try {
      data = await j("data/incidents.json");
    } catch (err) {
      note.innerHTML = '<div class="empty-state empty-state--error">Incident data unavailable - ' + esc(err.message) + "</div>";
      host.innerHTML = "";
      return;
    }
    var list = data.incidents || [];
    note.innerHTML = "Real anomalies caught by the automated pipeline - detected, annotated and published by scheduled CI. " +
      "Showing the most recent " + esc(data.retention_days) + " days (" + list.length + " incidents). " +
      'Raw data: <a class="link-underline" href="data/incidents.json">incidents.json</a>.';
    if (!list.length) {
      host.innerHTML = '<div class="empty-state">No anomalies recorded in the last ' + esc(data.retention_days) + " days.</div>";
      return;
    }

    var shown = Math.min(INCIDENT_PAGE, list.length);

    function paint() {
      var more = list.length - shown;
      host.innerHTML = '<div class="incident-list">' + list.slice(0, shown).map(incidentHTML).join("") + "</div>" +
        (more > 0
          ? '<p style="margin-top:var(--spacing-s)"><button type="button" class="link-underline" data-incident-more>' +
            "Show " + more + " older incident" + (more === 1 ? "" : "s") + "</button></p>"
          : "");
      bindJumpLinks(host);
      var btn = $("[data-incident-more]", host);
      if (btn) {
        btn.addEventListener("click", function () {
          shown = Math.min(shown + INCIDENT_PAGE, list.length);
          paint();
        });
      }
    }
    paint();
  }

  /* ---------- symbol bar ---------- */
  function renderSymbolBar(latest) {
    var bar = $("#symbol-bar");
    if (!bar) return;
    if (!latest || !latest.symbols) {
      bar.innerHTML = '<div class="empty-state">No instruments published yet.</div>';
      return;
    }
    var syms = Object.keys(latest.symbols);
    bar.innerHTML = syms.map(function (s) {
      return '<button type="button" data-file="' + esc(symbolFile(s)) + '" data-symbol="' + esc(s) + '" aria-pressed="false">' + esc(s) + "</button>";
    }).join("");
    $$("button", bar).forEach(function (b) {
      b.addEventListener("click", function () { loadPriceChart(b.dataset.file, b.dataset.symbol); });
    });
  }

  /* ---------- bootstrap ---------- */
  async function init() {
    renderMeta(null);
    renderCards(null);
    chartState("price", "loading", "loading price history...");
    chartState("corr", "loading", "loading correlation structure...");
    chartState("timeline", "loading", "loading anomaly timeline...");

    var latest = null;
    try {
      latest = await j("data/latest.json");
    } catch (err) {
      setStatus("error", "No live snapshot could be loaded - " + err.message);
    }
    state.latest = latest;
    renderMeta(latest);
    renderCards(latest);
    renderSymbolBar(latest);
    await loadCorrelation();

    try {
      state.anomalies = await j("data/anomalies.json");
      if (!state.anomalies || !state.anomalies.events) state.anomalies = { events: [] };
    } catch (err) {
      state.anomalies = { events: [] };
      chartState("timeline", "error", "Anomaly feed unavailable - " + err.message);
    }
    renderTimeline();
    await loadFeedback();
    renderAlerts();
    await loadPerformance();
    await loadIncidents();

    if (latest && latest.symbols) {
      var first = Object.keys(latest.symbols)[0];
      if (first) await loadPriceChart(symbolFile(first), first);
    } else {
      chartState("price", "empty", "No price history available until the first pipeline run.");
    }

    ["t-window", "t-severity"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener("change", renderTimeline);
    });
    var tScore = document.getElementById("t-score");
    if (tScore) tScore.addEventListener("input", function (e) {
      var out = document.getElementById("t-score-val");
      if (out) out.textContent = (e.target.value / 100).toFixed(2);
      renderTimeline();
    });
    var rSev = document.getElementById("r-severity");
    if (rSev) rSev.addEventListener("change", renderAlerts);
    var rSort = document.getElementById("r-sort");
    if (rSort) rSort.addEventListener("change", renderAlerts);
    var rScore = document.getElementById("r-score");
    if (rScore) rScore.addEventListener("input", function (e) {
      var out = document.getElementById("r-score-val");
      if (out) out.textContent = (e.target.value / 100).toFixed(2);
      renderAlerts();
    });

    window.addEventListener("resize", function () {
      [state.priceChart, state.corrChart, state.timelineChart].forEach(function (c) {
        if (c) c.resize();
      });
    });

    // Re-read the published snapshot on the documented interval. The
    // publisher itself runs every 30 minutes, so a full reload is correct.
    setInterval(function () { location.reload(); }, POLL_MS);
    if (window.CPReveal) window.CPReveal.refresh();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
