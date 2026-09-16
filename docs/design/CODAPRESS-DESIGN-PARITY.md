# Design parity report — codapress.co.uk → Cross-Asset Anomaly Monitor

> **Historical note (2026-09-16).** The landing page described in this report was
> replaced by the user's own page later the same day, restored verbatim from
> `cross-asset-anomaly-monitor-v6.html`. Every dashboard section below remains
> accurate. The motion layer added to the dashboard is documented separately in
> `MOTION-SPEC.md` and `MOTION-HANDOFF.md`.

Reference: **https://codapress.co.uk/**
Method: live inspection with Playwright/Chromium on 2026-09-16 — computed styles per
element role, the full CSS custom-property sheet, DOM geometry, canvas/cursor
construction, hover deltas, a scroll probe, 13 screenshots at 1440/768/390, and an
independent visual description pass over those screenshots.
Raw evidence: `design-extraction/codapress/` (working tree, not committed).
Token sheet: [`CODAPRESS-DESIGN-SPEC.md`](CODAPRESS-DESIGN-SPEC.md).

No reference copy, imagery, logo, photography or source code is reused. What is
reproduced is the **design language**: tokens, structure, component grammar and
behaviour.

---

## 1. Type and colour tokens

| Reference token | Reference value | This build | Status |
|---|---|---|---|
| `--color-bg` | `#000000` | `#000000` | identical |
| `--color-text` | `#ffffff` | `#ffffff` | identical |
| `--color-mid-grey` | `#8F8F8F` | `#8F8F8F` | identical |
| `--color-muted` | `#575757` | `#575757` | identical |
| `--color-btn` | `#333333` | `#333333` | identical |
| `--color-link-hover` | `#ffffff` | `#ffffff` | identical |
| `--cursor-hover-color` | `#48ff00` | `#48ff00` | identical |
| cookie panel | `#1a1a1a` | `#1a1a1a` (`--color-cookie-bg`) | identical |
| font family | `JetBrains Mono` only (400/700) | `JetBrains Mono` only (400/700) | identical |
| `--h1-size` | `clamp(2rem,3.968lvw,152px)` | same | identical |
| `--h2-size` | `clamp(1.5rem,2.646lvw,101.5px)` | same | identical |
| `--text-xs…--text-4xl` | 13 fluid steps | same 13 steps, same values | identical |
| `--lede-size` | `clamp(1.2rem,1.693lvw,64.96px)` | same | identical |
| `--h1-leading` / `--h2-leading` | `1.15` / `.9` | `1.15` / `.9` | identical |
| `--mobile-menu-text-size` | `clamp(40px,2.307lvw,60px)` | same | identical |
| `lvw` units | used throughout | used, with a `vw` fallback declaration first | identical + hardened |
| measured h1 at 1440×900 | 57.14px / 65.71px | 57.14px / 65.71px | identical |
| measured h2 at 1440×900 | 38.10px / 34.29px | 38.10px / 34.29px | identical |
| card title | 13px / weight 700 | 13px / weight 700 | identical |

## 2. Spacing, radii and layout

| Reference | Value | This build | Status |
|---|---|---|---|
| spacing scale | `--spacing-2xs…4xl`, 9 steps | same, same values | identical |
| `--gutter` | `clamp(24px,3.307lvw,127px)` | same | identical |
| `--gutter-lg` / `--gutter-xl` | `6.614lvw` / `13.228lvw` clamps | same | identical |
| `--grid-gap` | `clamp(24px,3.307lvw,127px)` | same | identical |
| `--radius-s` | `clamp(4px,.331lvw,12.7px)` | same | identical |
| radius everywhere else | `0` | `0` | identical |
| shadows | none | none | identical |
| `--stable-vh` | `900px`; heights derived as `calc(900px * n)` | same mechanism, same constants | identical |
| `--spacing-footer-top` | `calc(900px*.09921)` | same | identical |
| breakpoints | 480 / 768-769 / 900 / 1024 / 1200 / 1540 | same | identical |
| hero title inset | `--spacing-card` (23.81px @1440) | `--spacing-card` | identical |
| container model | full-bleed with `vw` gutters, not a centred max-width | same | identical |
| section padding | `--spacing-2xl` block padding | same | identical |

## 3. Section-by-section structure

| Reference section (measured @1440) | This build | Status |
|---|---|---|
| `header.header` — fixed, transparent, `top:10px`, `width: calc(100vw - 20px)`, z-index 20 | same geometry and behaviour | matched |
| nav links — uppercase mono, `--text-sm`, transparent 1px bottom border, hover → white underline | same | matched |
| `section.home-hero` — full-bleed, `min-height: var(--stable-vh)`, black frame | same, `border: 10px solid #000` | matched |
| dither plasma canvas at `.92` opacity + vignette that fades out when ready | same, original WebGL2 field | matched (own implementation) |
| `<h1>` centred, `--h1-size`, weight 400, 3 short lines, resolved inside the canvas | same; DOM `<h1>` retained as the a11y + no-WebGL fallback | matched |
| `section.home-lede` — asymmetric block, plain body copy, no heading | same | matched |
| section header — title left, action link right, `--h2-size` weight 400 | same, with an added `01 —` eyebrow index | matched (+ index) |
| arc carousel — `--arc-radius:2800px`, `--arc-angle-step:8deg`, card `clamp(180px,31.5vw,270px)`, viewport `clamp(288px,46.8vh,504px)` | same values | identical |
| card — tags row, `aspect-ratio:224/335` cover, `1px solid #575757` hairline, `--radius-s`, text block `padding-top: --spacing-card` | same | identical |
| 16:9 featured block | same (`aspect-ratio:16/9`, hairline border) | matched |
| 4-column square record grid, hairline white frame, title/excerpt/underlined link | same | matched |
| footer — brandmark + copyright left, links + socials right, `min-height:44px` link rows, hover underline / `opacity:.7` | same | matched |
| bottom-right cookie notice, `#1a1a1a`, underlined secondary link, white accept button that inverts | same | matched |

Deliberate additive changes (not deviations from the language):

- the arc cards carry **real live data** — a sparkline drawn from the published
  `data/charts/<symbol>.json` series, the live price, the 1-month return and the
  current z-score, instead of static cover art;
- section headers carry a small `01 —` index, consistent with the reference's
  mono-label grammar;
- the landing page adds a hero status line and a "Method" grid so the page
  explains the product, not just presents it.

## 4. Components

| Component | Reference | This build | Status |
|---|---|---|---|
| filled button | `#333` bg, white text, radius 0, hover → white bg / black text, `transition: background .2s, color .2s` | identical | identical |
| plain link button | text only, underline on hover | identical | matched |
| chips | mono, uppercase, hairline border, sharp corners | identical (monochrome severity ladder white → `#575757`) | matched |
| tables | hairline row borders, uppercase muted header | identical, styled to the same tokens | matched |
| empty state | hairline panel, muted copy | identical, plus a white-bordered error variant | matched + extended |
| skip link | fixed, white on black, `translateY(-120%)`, revealed on focus | identical | identical |
| focus ring | `outline: 2px solid` + offset on interactive elements | identical | identical |

## 5. Motion and interaction

| Behaviour | Reference | This build | Status |
|---|---|---|---|
| smooth scroll | Lenis 1.3.x | dependency-free RAF lerp (`lenis-lite.js`), same easing feel; disabled for `prefers-reduced-motion` | matched (own implementation) |
| reveals | elements start `opacity:0; translateY(10%)`, class flip on entry, scroll-driven (GSAP ScrollTrigger) | identical initial state, driven by `IntersectionObserver` | matched (own implementation) |
| card row motion | scroll-linked, no autoplay marquee, no edge-fade mask, hard `overflow:hidden` crop | drag + keyboard, settles to the nearest card, no autoplay, no edge mask | matched |
| custom cursor | two `mix-blend-mode: difference` shapes (30×34 / 10×18), z 100/101, label state turns the big shape `#48ff00` at `.9` | identical construction, label `DRAG` on the carousel; disabled on coarse pointers | identical |
| nav hover | 1px bottom border → white | identical | identical |
| navbar scroll behaviour | never shrinks, hides, blurs or gains a background | identical | identical |
| page-load sequence | content reveals after the shell paints; the hero headline resolves out of the plasma | identical (1.6s resolve) | matched |
| reduced motion | reference ships none | ours honours it: static dithered frame, no cursor, no smooth scroll, no reveals | improvement |

## 6. Responsive

Measured on the local build against the real published data:

| Viewport | Page | Horizontal overflow | Console errors | Page errors |
|---|---|---|---|---|
| 1440×900 | `index.html` | 0px | 0 | 0 |
| 1440×900 | `dashboard.html` | 0px | 0 | 0 |
| 768×1024 | `index.html` | 0px | 0 | 0 |
| 768×1024 | `dashboard.html` | 0px | 0 | 0 |
| 390×844 | `index.html` | 0px | 0 | 0 |
| 390×844 | `dashboard.html` | 0px | 0 | 0 |

The reference collapses its nav to a hamburger below 769px; ours does the same and
also tightens the arc (`--arc-radius: 1100px`, `--arc-angle-step: 10deg`) so three
cards remain in view on a phone instead of one.

## 7. Accessibility

| Check | Result |
|---|---|
| Skip link | present on both pages, first tab stop, visible on focus |
| Tab order | 24 stops sampled per page; **0** without a visible focus indicator |
| Body contrast | lede `#8F8F8F` on `#000` = **6.49:1** (AA needs 4.5) |
| Muted labels | `#8F8F8F` on `#000` = **6.49:1** |
| Card labels / chips | `#8F8F8F` on `#020202` = **6.42:1** |
| Table header | **6.49:1**; table cells `#fff` on `#020202` = **20.75:1** |
| Section titles | **21:1** |
| Decorative art | every generated SVG is `aria-hidden` or carries `role="img"` + a label |
| Tables | real `<caption>` + `scope="col"` headers; alert rows are keyboard-selectable (`Enter` / `Space`) |
| Reduced motion | honoured end to end |

## 8. Behavioural surface (dashboard)

| State | How it was induced | Observed result |
|---|---|---|
| Nominal | real `data/*.json` from the live publish | 7 asset cards, 136 alert rows, 136 incidents, live status line, 0 console errors |
| No data | every `/data/**` request aborted | explicit copy per panel: "No market snapshot published yet.", "No price history available until the first pipeline run.", "No anomalies recorded yet.", "Incident data unavailable – …"; error banner in the hero; **0 page errors** |
| Server error | `data/latest.json` forced to `500` | hero shows the error banner, overview shows the empty state, the rest degrade independently; **0 page errors** |
| Empty payload | valid JSON with zero events / zero incidents | "No anomalies have been detected yet.", "No anomalies recorded in the last 60 days." |

## 9. Weight and budget

| Page | Requests | Total | Third-party |
|---|---|---|---|
| `index.html` | 22 | 386.7 KB (274 KB of it the 7 chart JSONs) | Google Fonts 30.7 KB |
| `dashboard.html` | 19 | 578.5 KB | ECharts 325.1 KB (jsDelivr) + Google Fonts 30.7 KB |

No paid service, no API key, no build step, no bundler. Both pages are static files.

## 10. Hero effect evidence

A screenshot of the rendered `.dither-hero` was decoded pixel-by-pixel:

| Metric | Value | Reading |
|---|---|---|
| size | 1420 × 880 | full hero, matching the reference's 1420 × 880 canvas |
| mean luminance | 101.24 | mid-grey field, as on the reference |
| luminance std-dev | 49.47 | strong structure, not a flat fill |
| chroma | 0.373% of pixels | effectively monochrome, as on the reference |
| dominant levels | 26/27, 44-48, 65, 86, 109, 133, 156, 181, 207 | the measured grey ladder `{0,27,48,71,94,119,145,171,198,226,255}` plus the intermediate values produced by the anti-aliased headline mask |

## 11. Deliberate deviations

1. **Libraries replaced.** The reference runs GSAP + ScrollTrigger + Lenis + an
   Astro build. This build ships hand-written CSS and vanilla JS with a single
   ECharts CDN dependency on the dashboard, so the site stays free, static and
   rebuildable with no toolchain.
2. **Fonts.** Same family (JetBrains Mono, OFL). Served from Google Fonts rather
   than self-hosted during this pass; self-hosting is a drop-in change.
3. **Content and imagery.** None of the reference's copy, covers, photography or
   logo is used. Card artwork is generated from this project's own data.
4. **Reference gaps.** The reference exposes no `prefers-reduced-motion` handling
   and no explicit error/empty states. This build adds both, because a live data
   product needs them.

## 12. Screenshot index

Reference (in `docs/design/reference/`):

| File | Shows |
|---|---|
| `codapress-desktop-hero.png` | dithered plasma hero, transparent header, headline inside the field |
| `codapress-desktop-card-row.png` | arc card row in a content section |
| `codapress-desktop-records.png` | square record grid |
| `codapress-mobile-hero.png` | 390px hero |

This build (`docs/screenshots/`):

| File | Shows |
|---|---|
| `landing-desktop.png` | 1440 hero, dithered field with the resolved headline |
| `landing-tablet.png` | 768 hero with the hamburger nav |
| `landing-mobile.png` | 390 full page: hero, lede, arc row, featured block, method grid, incidents, footer |
| `dashboard-desktop.png` | 1440 dashboard: status line, asset grid, panels, hairline tables |
| `dashboard-mobile.png` | 390 dashboard |
