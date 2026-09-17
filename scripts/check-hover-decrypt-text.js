#!/usr/bin/env node
/* Exact-text regression coverage for every static dashboard scramble target
   and representative dynamically rendered controls. No server or live feed needed.
   Usage: node scripts/check-hover-decrypt-text.js [motionScript]
*/
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ reducedMotion: "no-preference" });
    await page.route("**/*", route => route.abort());
    const root = path.join(__dirname, "..");
    const html = fs.readFileSync(path.join(root, "site/dashboard.html"), "utf8")
      .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "")
      .replace(/<link\b[^>]*>/gi, "");
    await page.setContent(html);
    await page.evaluate(() => {
      document.documentElement.classList.remove("entrance-pending");
      document.documentElement.removeAttribute("data-motion");
      // Advance animation frames explicitly so every restart happens mid-scramble.
      let now = 0;
      let nextId = 0;
      const frames = new Map();
      window.requestAnimationFrame = fn => { frames.set(++nextId, fn); return nextId; };
      window.cancelAnimationFrame = id => frames.delete(id);
      window.advanceScrambleFrame = ms => {
        now += ms;
        const batch = Array.from(frames.values());
        frames.clear();
        batch.forEach(fn => fn(now));
      };
      Math.random = () => 0;
      window.IntersectionObserver = class { observe() {} unobserve() {} };
      window.matchMedia = query => ({ matches: !query.includes("reduced-motion") });
    });
    await page.addScriptTag({ path: process.argv[2] || path.join(root, "site/assets/js/motion.js") });
    // These fixtures mirror dashboard.js output and exercise late-injection binding.
    await page.evaluate(() => {
      document.querySelector("#cards").innerHTML = '<button class="asset-card"><span class="asset-card__label">BTC-USD</span><span class="asset-card__price">123.45</span></button>';
      document.querySelector("#symbol-bar").innerHTML = '<button aria-pressed="false">^GSPC</button>';
      document.querySelector("#alerts tbody").innerHTML = '<tr data-selectable tabindex="0"><td>2026-09-17</td><td>EURUSD=X</td><td>0.85</td></tr>';
      document.querySelector("#incidents-host").innerHTML = '<a class="link-underline" href="#assets" data-jump="BTC-USD">View price chart</a><button data-incident-more>Show 12 older incidents</button>';
      document.querySelector("#incidents-note").innerHTML = 'Raw data: <a href="data/incidents.json">incidents.json</a>.';
    });
    await page.waitForFunction(() => document.querySelector("[data-incident-more]").dataset.scrambleBound === "1", null, { polling: 50 });
    const result = await page.evaluate(() => {
      // Settle count-up frames before testing labels.
      advanceScrambleFrame(0);
      advanceScrambleFrame(1000);
      const targets = Array.from(document.querySelectorAll('[data-scramble-bound="1"]'));
      const failures = [];
      let checks = 0;
      function check(ok, name) { checks++; if (!ok) failures.push(name); }
      for (const host of targets) {
        const node = CPScramble.labelFor(host);
        const original = node.textContent;
        const originalHTML = host.innerHTML;
        const aria = host.getAttribute("aria-label");
        const name = original.trim();
        const enter = () => host.dispatchEvent(new MouseEvent("mouseenter"));
        const verify = phase => {
          check(node.textContent === original, `${name}: ${phase} text`);
          check(host.getAttribute("aria-label") === aria, `${name}: ${phase} aria`);
          check(host.innerHTML === originalHTML, `${name}: ${phase} child markup`);
          check(CPScramble.stats().active === 0, `${name}: ${phase} idle`);
        };
        enter();
        advanceScrambleFrame(1);
        check(node.textContent !== original, `${name}: effect actually runs`);
        for (let i = 0; i < 8; i++) {
          host.dispatchEvent(new MouseEvent("mouseleave"));
          enter();
          advanceScrambleFrame(1);
        }
        advanceScrambleFrame(500);
        verify("rapid re-entry");
        for (const event of ["blur", "pointerdown"]) {
          enter();
          advanceScrambleFrame(1);
          enter();
          advanceScrambleFrame(1);
          host.dispatchEvent(new Event(event));
          verify(event);
          advanceScrambleFrame(500);
          verify(`${event} after cancelled frames`);
        }
        // Force keyboard-focus detection independently of layout/visibility.
        const matches = host.matches;
        host.matches = selector => selector === ":focus-visible" || matches.call(host, selector);
        enter();
        advanceScrambleFrame(1);
        host.dispatchEvent(new FocusEvent("focus"));
        advanceScrambleFrame(1);
        advanceScrambleFrame(500);
        delete host.matches;
        verify("hover interrupted by keyboard focus");
      }
      check(document.querySelectorAll('.raw-links [data-scramble-bound="1"]').length === 6, "all six final-section links covered");
      check(document.querySelectorAll('.footer__link[data-scramble-bound="1"]').length === 3, "all three footer links covered");
      check(!document.querySelector('.footer__social[data-scramble-bound="1"]'), "social icons excluded");
      // Separate controls must not cancel or contaminate one another.
      const pair = targets.slice(0, 2).map(host => ({ host, text: CPScramble.labelFor(host).textContent }));
      pair.forEach(({ host }) => CPScramble.run(host));
      advanceScrambleFrame(1);
      CPScramble.run(pair[0].host);
      advanceScrambleFrame(1);
      advanceScrambleFrame(500);
      pair.forEach(({ host, text }) => check(CPScramble.labelFor(host).textContent === text, "concurrent labels restored"));
      check(CPScramble.stats().active === 0, "concurrent animations idle");
      return { targets: targets.length, checks, failures };
    });
    console.log(JSON.stringify(result, null, 2));
    assert.deepEqual(result.failures, [], "Scramble must restore exact original labels");
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
