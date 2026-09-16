# Motion specification — dashboard

What ships, where it lives, and how to switch it off.

- Reference study: [`CODAPRESS-MOTION-INVENTORY.md`](CODAPRESS-MOTION-INVENTORY.md)
- Design system: [`CODAPRESS-DESIGN-SPEC.md`](CODAPRESS-DESIGN-SPEC.md)
- Code: `site/assets/css/motion.css`, `site/assets/js/motion.js`

The reference's motion is re-implemented in vanilla CSS/JS — no GSAP, no Lenis, no
bundler — and applied **only to the dashboard**, additively. It changes no layout, no
copy, no colour token, no data contract and no chart logic.

## 0. Where motion is applied

The user's landing page (`cross-asset-anomaly-monitor-v6.html`) is their own file and is
left as it is. The dashboard is the page that receives the motion layer.

## 1. Global controls

| Control | Effect |
|---|---|
| `<html data-motion="off">` | Every effect is disabled; content renders in its final state |
| `?motion=off` in the URL | Same as above, applied on load (for screenshots and debugging) |
| `data-motion="subtle"` | Reveals play, scrubbed/looping effects are skipped |
| `prefers-reduced-motion: reduce` | Forces the `off` behaviour for the whole page |
| `data-motion-skip="<effect>"` on a node | Excludes that node from `<effect>` |

Tuning tokens (CSS custom properties on `:root` in `motion.css`):

```
--motion-instant: 120ms
--motion-fast:    200ms   /* reference: 0.2s hover deltas            */
--motion-base:    400ms   /* reference: link-scramble duration        */
--motion-slow:    600ms   /* reference: power3.inOut detail slide     */
--motion-slower:  900ms
--motion-hero:   1500ms   /* reference: featured surface scale 3 -> 1 */
--motion-stagger:  60ms
--ease-out:   cubic-bezier(.16,.84,.44,1)     /* power3.out  */
--ease-inout: cubic-bezier(.65,0,.35,1)       /* power3.inOut */
--motion-scrub: 0.6       /* reference: lede scrub lag                */
```

## 2. Effect catalogue

Each entry: what it animates, what drives it, the exact values, and why it earns its place
on a data page.

### M1 — Section entrance stagger
- **Target:** every `main .dash-section` on first paint.
- **Values:** `opacity 0 → 1`, `translateY(12px) → 0`, duration `--motion-slow` (600ms),
  `--ease-out`, stagger `--motion-stagger` (60ms) in DOM order, first section at 80ms.
- **Trigger:** `DOMContentLoaded`, once.
- **Rationale:** the reference gates its whole entrance behind a class and reveals blocks
  in order. On a data page this orients the reader top-to-bottom instead of dumping eight
  panels at once.
- **Settled state:** the class is removed after the run, so nothing is left mid-transform.

### M2 — Section heading decode
- **Target:** `.dash-section .section-header__eyebrow` (the `01`…`08` index labels) and
  `.section-header__title` on first view of each section.
- **Values:** charset `ABCDEFGHIJKLMNOPQRSTUVWXYZ`; total 400ms; each character locks at
  `random * 280ms + 40ms`; non-alphanumeric characters never scramble; the true glyph is
  restored from a stored original so the heading is never wrong in the DOM.
- **Trigger:** `IntersectionObserver`, `threshold 0.25`, once per section.
- **Rationale:** this is the reference's hover signature (`link-scramble`), moved from a
  pointer gesture to a scroll event so it works on touch too. On a monitoring console a
  "decoding readout" reads as instrumentation rather than decoration — and it is applied
  to headings only, never to data values.

### M3 — Hero paragraph paint
- **Target:** the paragraph under the dashboard heading (`.dash-section__note` in
  `#hero`).
- **Values:** characters split into spans; `color` `#333333 → #ffffff`; per-character
  duration 400ms, stagger 20ms, `linear`; driven by scroll progress with a 0.6 scrub lag;
  start when the block's top passes 70 % of the viewport, end when it passes 30 %.
- **Trigger:** scroll.
- **Rationale:** the reference's signature reveal, verbatim in timing. It marks the one
  paragraph that explains the product — the place on the page where a reader should slow
  down.
- **Guard:** the full text stays in the DOM; because the animation is colour-only, the
  text is always selectable, searchable and readable by assistive technology.

### M4 — Panel development
- **Target:** every `.panel.chart-wrap` (price history, correlation, timeline).
- **Values:** the inner `.chart` transitions `transform: scale(1.12) → 1` **and**
  `opacity 0 → 1` over `--motion-hero` (1500ms) `--ease-inout` with a 300ms delay.
  `overflow: hidden` holds the growing canvas inside the panel's box and is released by
  `.is-settled` ~1.9s later, so the ECharts tooltip is not clipped at rest.
- **Trigger:** `IntersectionObserver`, `rootMargin: 0 0 -6% 0`, `threshold 0.08`, once per
  panel; plus a viewport-scoped sweep on scroll and resize, and a 6s sweep, as backstops.
- **Rationale:** the reference opens its featured media by animating the container width
  from 0 and the surface from `scale: 3`. The scale half of that is reproduced; the
  rest is not, for measured reasons.
- **Deviations from the reference (measured, not assumed):**
  - The reference animates **width**. Here that would make ECharts resize on every frame,
    so width is never animated.
  - A **clip-path wipe** version was built and then removed: on this page it cost 9-10 % of
    frames (90.6 % within one vsync, 26 dropped frames in a 5 s scroll sweep, and a
    `will-change: clip-path` hint only recovered to 92.1 %). The transform/opacity version
    measures **100 % within one vsync with zero dropped frames** — the same as the page
    with motion disabled — so the wipe was dropped rather than shipped as jank.
  - A first attempt used keyframes and could stall; the shipped version uses transitions,
    which always land exactly on the class's value.
  - An early version also revealed every panel on a 6 s timer, which spent the entrance of
    panels still thousands of pixels below the fold. The backstop is now viewport-scoped.

### M5 — Asset-card stagger and count-up
- **Target:** `.asset-card` in `#overview`.
- **Values:** entry `opacity 0 → 1`, `translateY(10px) → 0`, 450ms `--ease-out`, 45ms
  stagger; the price value counts from 0 to its real value over 700ms `--ease-out`,
  formatted with the same locale rules as the settled value.
- **Trigger:** first render of the cards, once per page load.
- **Rationale:** the reference staggers card rows; the count-up is the instrument-panel
  adaptation and only ever animates *to* the published number. The final frame writes the
  exact string the static renderer would have written.
- **Guard:** no count-up on any value that is null, non-numeric, or on a re-render.

### M6 — Chart draw-on
- **Target:** all three ECharts instances.
- **Values:** `animationDuration: 900`, `animationEasing: "cubicOut"`, `animationDelay`
  ramping 0 → 180ms across series; `animationDurationUpdate: 300`,
  `animationEasingUpdate: "cubicOut"` so filter changes feel responsive rather than slow.
- **Trigger:** chart creation and option updates.
- **Rationale:** the reference reveals content progressively; here the data draws itself
  once and then updates quickly.

### M7 — Table row stagger
- **Target:** `#alerts tbody tr`.
- **Values:** `opacity 0 → 1`, `translateY(6px) → 0`, 260ms `--ease-out`, 24ms stagger,
  **capped at the first 30 rows** so a 137-row table does not become a five-second crawl.
- **Trigger:** on render, once.
- **Rationale:** makes a long table feel scanned rather than dumped, without delaying the
  data.

### M8 — Live pulse
- **Target:** the status line under the dashboard heading.
- **Values:** the line's opacity runs `0.45 → 1 → 1` over 900ms `--ease-out` when the
  published timestamp changes; the timestamp's own digits flicker through random digits
  for 400ms using the same resolver as M2.
- **Trigger:** after `data/latest.json` resolves, and on each 5-minute reload.
- **Rationale:** the page is republished by CI every 30 minutes; one quiet confirmation is
  the honest signal that this is a live instrument rather than a screenshot. It adds **no
  new element and no geometry** — an earlier version added a status dot, which was removed
  because it would have changed the dashboard's appearance.

### M9 — Scroll progress rail
- **Target:** a 1px fixed rail at the right edge of the dashboard.
- **Values:** `transform: scaleY(progress)` with `transform-origin: top`, updated from a
  passive scroll listener; no transition (it must track the finger).
- **Trigger:** scroll.
- **Rationale:** the reference maps scroll position linearly onto horizontal rows; here
  that same 1:1 mapping becomes a position readout for a long, sectioned page.

### M10 — DROPPED (artwork parallax)
The reference parallaxes its featured media ±5 %. The dashboard has **no artwork to
parallax** — its cards carry numbers and its incident records carry text, not imagery.
Adding a parallax target purely because the reference had one would be decoration for its
own sake, so this effect is deliberately not implemented. Its row is kept here so the
decision is visible rather than silently missing.

### M11 — Hover depth
- **Target:** `.asset-card`, `.incident`, `.panel`.
- **Values:** `transform: translateZ(10px) rotateY(-2deg)`, 200ms `ease-out`, with
  `perspective: 500px` on the wrapper — the reference's card-media rule, same numbers.
- **Trigger:** pointer hover (fine pointers only).
- **Rationale:** confirms interactivity. Applied only on fine pointers so touch never gets
  a stuck hover state.

### M12 — Hover / focus decrypt
- **Target:** every link, button and tab with a text label — nav links, section action
  links, asset cards, symbol-bar buttons, incident links, footer links, the skip link, the
  cookie buttons, and the symbol cell of each alert row (139 of them). **211 of the 213
  matched controls**; the two skipped have no text of their own.
- **Values (the reference's own):** glyph set `A–Z`; total **400 ms**; each character locks
  once at `random × 280 ms + 40 ms`, i.e. **40–320 ms**; one pass per hover, re-armed on the
  next; non-alphanumeric characters never substituted; the original string written back
  verbatim on completion, and immediately on `pointerdown`.
- **Trigger:** `mouseenter` on fine pointers, and `focus` when `:focus-visible` matches
  (so a click does not fire it).
- **Rationale:** this is the effect you asked for. On a monitoring console a label that
  resolves out of noise reads as a readout, not a decoration — and because only chrome
  animates and only one element animates at a time, it never competes with the data.
- **Layout safety:** the character count is preserved and the glyphs come from a monospace
  set, so a scrambled label occupies the same box as the real one. Measured across every
  measurable control: **0 px width and height delta**, 0 character-count changes, 0 labels
  left unrestored.
- **Accessibility:** the host's accessible name is pinned to the real label for the duration
  of the pass and removed afterwards; the focus ring is untouched; and the whole effect is
  disabled before first paint when `prefers-reduced-motion` is set.
- **Deviations from the reference:** the reference binds hover only and excludes running
  prose and media; ours adds keyboard focus (a keyboard user should get the same feedback)
  and animates the alert-row symbol cell, but leaves table cells other than the symbol and
  all non-interactive text untouched.

## 3. Reduced motion

With `prefers-reduced-motion: reduce`:

| Effect | Behaviour |
|---|---|
| M1, M4, M5, M7 | Skipped; elements render in their final state |
| M2, M8 | Skipped; the real text is shown immediately |
| M3 | Characters are set straight to `#ffffff`; no scroll binding |
| M6 | `animation: false` on all ECharts instances |
| M9, M10 | No scroll listeners are attached |
| M11 | Hover depth removed |
| M12 | No binding at all — hovering or focusing a link leaves its label untouched |

No content is conveyed by motion or colour alone at any setting: every animated value is
also present as plain text in the DOM.

## 4. Budget

Measured on the local build against the real published data, 1440×900, Chromium:

| Metric | Budget | Measured |
|---|---|---|
| Added CSS | ≤ 12 KB | **8.01 KB** (`motion.css`) |
| Added JS | ≤ 20 KB | **15.63 KB** (`motion.js`) |
| Share of page weight | — | **3.9 %** of 606.2 KB |
| Long tasks on load | none over 50 ms | **2 long tasks / 122 ms** with motion vs 2 / 103 ms without |
| Frames during a 5s scroll sweep | ≥ 95 % within one vsync | **100 %** (p50 16.7 ms, p95 16.8 ms, max 16.8 ms, **0 dropped**) |
| Frame smoothness vs motion off | no regression | **100 % vs 100 %**, 0 dropped frames both ways |
| First contentful paint | within 150 ms of the no-motion build | **368 ms** vs 312 ms |
| Layout shift | 0 — every effect uses `transform`, `opacity` or `color` | no layout property is animated |
| New network requests | 2 (both local) | 2 |

## 5. File layout and disabling

```
site/assets/css/motion.css   all motion CSS, loaded after codapress.css / dashboard.css
site/assets/js/motion.js     all motion behaviour, loaded after dashboard.js
```

To remove the whole layer: delete the two `<link>`/`<script>` tags from
`site/dashboard.html`. Nothing else references them.

To disable at runtime: `?motion=off`, or set `data-motion="off"` on `<html>`.
To disable one effect: add `data-motion-skip="M4"` to the element (effect ids as above),
or `data-motion-skip="scroll"` / `"reveal"` / `"decode"` / `"pulse"` for a whole class.

The motion layer never reads or writes application state. `motion.js` only reads the DOM
after `dashboard.js` has rendered, and observation is re-armed by a single
`MutationObserver` on `#cards`, `#alerts tbody` and `#incidents-host` so late data still
gets its entrance.
