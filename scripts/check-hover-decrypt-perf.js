#!/usr/bin/env node
/* Frame-smoothness gate for the dashboard's hover/focus decrypt effect (M12).

   Usage:
     node scripts/check-hover-decrypt-perf.js <baseUrl> [outFile]

   Requires Playwright in the repo's node_modules (npm i playwright) and a
   static server already serving the build dir, e.g.:
     python -m http.server 8913 --dir <build>

   Exits 0 when every assertion passes, 1 otherwise. The JSON it writes is the
   recorded baseline in docs/design/motion-perf-baseline.json.

   What it asserts:
     - the decrypt effect does not cost frames versus the same page scrolled
       with no hovering
     - it introduces no long tasks
     - it leaves no animation loop or timer running once idle
*/
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const BASE = (process.argv[2] || "http://127.0.0.1:8913").replace(/\/$/, "");
const OUT = process.argv[3] || path.join(__dirname, "..", "docs", "design", "motion-perf-baseline.json");
const SAMPLE_MS = 6000;

const INIT = `
window.__f = []; window.__s = false; window.__lt = [];
(function l(t){ if (window.__s) window.__f.push(t); requestAnimationFrame(l); })(performance.now());
try {
  new PerformanceObserver(function (x) { x.getEntries().forEach(function (e) { window.__lt.push(e.duration); }); })
    .observe({ entryTypes: ['longtask'] });
} catch (e) {}
/* the dashboard reloads itself every 5 minutes by design; neutralise only long
   intervals so a measurement pass is not interrupted */
(function () { var o = window.setInterval;
  window.setInterval = function (fn, ms) { return ms >= 60000 ? 0 : o.apply(this, arguments); }; })();
`;

async function sample(page) {
  const frames = await page.evaluate(() => { window.__s = false; return window.__f; });
  const d = [];
  for (let i = 1; i < frames.length; i++) d.push(frames[i] - frames[i - 1]);
  d.sort((a, b) => a - b);
  const dropped = d.filter(x => x > 20).length;
  const longTasks = await page.evaluate(() => window.__lt);
  return {
    frames: d.length,
    p50: +d[Math.floor(d.length * 0.5)].toFixed(1),
    p95: +d[Math.floor(d.length * 0.95)].toFixed(1),
    maxMs: +d[d.length - 1].toFixed(1),
    dropped,
    withinVsyncPct: +((100 * (d.length - dropped)) / d.length).toFixed(1),
    longTasks: longTasks.length,
    longTaskMs: +longTasks.reduce((a, b) => a + b, 0).toFixed(1)
  };
}

(async () => {
  const browser = await chromium.launch();
  const result = { base: BASE, sampleMs: SAMPLE_MS, runs: {}, assertions: [] };

  for (const mode of ["baseline", "decryptStorm"]) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await ctx.addInitScript(INIT);
    const page = await ctx.newPage();
    page.setDefaultTimeout(8000);
    await page.goto(BASE + "/dashboard.html", { waitUntil: "load", timeout: 60000 });
    await page.waitForTimeout(4000);
    await page.evaluate(() => { window.__f = []; window.__lt = []; window.__s = true; });

    const t0 = Date.now();
    if (mode === "baseline") {
      while (Date.now() - t0 < SAMPLE_MS) { await page.mouse.wheel(0, 380); await page.waitForTimeout(120); }
    } else {
      const selectors = [".nav-link", ".section-header__action", ".symbol-bar button", ".asset-card", ".footer__link"];
      let i = 0;
      while (Date.now() - t0 < SAMPLE_MS) {
        const handles = await page.$$(selectors[i % selectors.length]);
        if (handles.length) {
          try { await handles[(i * 3) % handles.length].hover({ timeout: 1200 }); } catch (e) { /* off-screen */ }
        }
        i++;
        await page.waitForTimeout(60);
      }
      await page.mouse.move(3, 3);
    }

    const metrics = await sample(page);
    await page.waitForTimeout(1500);
    const idle = await page.evaluate(() => window.CPScramble.stats());
    result.runs[mode] = Object.assign({}, metrics, { idle });
    await ctx.close();
  }

  await browser.close();

  const b = result.runs.baseline, s = result.runs.decryptStorm;
  const checks = [
    ["decrypt does not drop frames versus baseline", s.withinVsyncPct >= b.withinVsyncPct - 3,
      s.withinVsyncPct + "% within one vsync vs " + b.withinVsyncPct + "% baseline, " + s.dropped + " dropped"],
    ["decrypt stays above 95% within one vsync", s.withinVsyncPct >= 95, s.withinVsyncPct + "%"],
    ["no long tasks introduced", s.longTaskMs <= b.longTaskMs + 50,
      s.longTaskMs + "ms vs " + b.longTaskMs + "ms baseline"],
    ["no animation loop left running when idle", s.idle.active === 0, JSON.stringify(s.idle)]
  ];
  result.assertions = checks.map(function (c) { return { name: c[0], pass: !!c[1], detail: c[2] }; });

  console.log("=== frame smoothness (" + (SAMPLE_MS / 1000) + "s sample each) ===");
  Object.keys(result.runs).forEach(function (k) {
    const r = result.runs[k];
    console.log(k.padEnd(14) + " frames=" + String(r.frames).padStart(4) +
      "  p50=" + String(r.p50).padStart(5) + "ms  p95=" + String(r.p95).padStart(5) + "ms" +
      "  max=" + String(r.maxMs).padStart(6) + "ms  dropped=" + String(r.dropped).padStart(2) +
      "  withinVsync=" + r.withinVsyncPct + "%  longTasks=" + r.longTasks + " (" + r.longTaskMs + "ms)");
  });
  console.log("\n=== assertions ===");
  let failed = 0;
  result.assertions.forEach(function (a) {
    if (!a.pass) failed++;
    console.log((a.pass ? "PASS  " : "FAIL  ") + a.name + "  ::  " + a.detail);
  });
  console.log("\n" + (result.assertions.length - failed) + "/" + result.assertions.length + " assertions passed");

  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify(result, null, 2));
  console.log("recorded baseline -> " + OUT);
  process.exit(failed ? 1 : 0);
})().catch(function (e) { console.error("FAIL", e); process.exit(1); });
