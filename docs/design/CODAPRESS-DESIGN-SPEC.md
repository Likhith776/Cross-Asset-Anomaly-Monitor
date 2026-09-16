# CODAPRESS DESIGN PARITY SPEC

Source of truth for the revamp of the Cross-Asset Anomaly Monitor web presence.
Reference: https://codapress.co.uk/ (live, inspected 2026-09-16 with Playwright/Chromium).

Everything below is **measured from the live reference site** (computed styles, CSS
custom properties, DOM geometry, screenshots). Nothing in this file is guessed.
No reference copy, imagery, logo or source code is reused: only the *design
language* (tokens, structure, behaviour) is reproduced for this project's content.

Extraction artefacts live in `design-extraction/codapress/`:
- `meta.json`            page/platform fingerprint (Astro, en-GB, Lenis smooth scroll)
- `css-vars.json`        every CSS custom property (the token sheet, verbatim)
- `role-styles.json`     computed styles per element role at 1440px
- `geometry.json`        absolute section geometry (y / height / width / padding)
- `dom-outline.json`     section order, headings, nav, links
- `canvas-cursor.json`   WebGL layers + custom cursor construction
- `hover-states.json`    measured hover deltas
- `scroll-probe.json`    scroll behaviour over 5 scroll positions
- `inline-styles.css`    component CSS (scoped Astro styles)
- `desktop-*.png`, `tablet-full.png`, `mobile-full.png`  reference screenshots
- `recognition.json`     independent visual descriptions of those screenshots

---

## 1. Identity

- Platform: static site, black canvas, one typeface, one accent.
- Mood: technical publisher. Monospace-only, sharp corners, generous black space.
- Language: `lang="en-GB"`.

## 2. Colour tokens (exact)

| Token | Value | Role |
|---|---|---|
| `--color-bg` | `#000000` | page background |
| `--color-text` | `#ffffff` | primary text |
| `--color-mid-grey` | `#8F8F8F` | secondary text |
| `--color-muted` | `#575757` | muted text / card hairlines |
| `--color-btn` | `#333333` | filled button background |
| `--color-link-hover` | `#ffffff` | link hover colour |
| `--cursor-hover-color` | `#48ff00` | ONE accent - custom-cursor label only |
| `--color-input-bg` | `#ffffff` | form field fill |
| `--color-input-text` | `#000000` | form field text |
| `--color-file-name` | `#979797` | file-button label |
| cookie panel bg | `#1a1a1a` | cookie notice |

Discipline: the accent `#48ff00` appears **only** inside the custom cursor. Links do
not change hue on hover (they are already white); footer links gain an underline;
filled buttons invert (bg -> `#ffffff`, text -> `#000000`) over `200ms`.

## 3. Typography

**One family only:** `JetBrains Mono` (`--font-mono` = `--font-nav`), body weight 400,
card/section titles weight 700. Google Fonts is acceptable (already a free, keyless
dependency); self-hosting is preferred if a license-clean copy is available.

### Fluid scale (verbatim from the reference `:root`)

All sizes are `clamp(min, <x>lvw, max)`. `lvw` = large viewport width unit.

| Token | Value |
|---|---|
| `--text-xs` | `clamp(13px, .728lvw, 27.9px)` |
| `--text-sm` | `clamp(13px, .86lvw, 33px)` |
| `--text-base` | `clamp(15px, .992lvw, 38.1px)` |
| `--text-md` | `clamp(18px, 1.19lvw, 45.7px)` |
| `--text-lg` | `clamp(20px, 1.323lvw, 50.8px)` |
| `--text-xl` | `clamp(22px, 1.455lvw, 55.8px)` |
| `--text-2xl` | `clamp(24px, 1.587lvw, 60.9px)` |
| `--text-3xl` | `clamp(28px, 2.646lvw, 101.5px)` |
| `--text-4xl` | `clamp(32px, 3.571lvw, 137px)` |
| `--h1-size` | `clamp(2rem, 3.968lvw, 152px)` |
| `--h2-size` | `clamp(1.5rem, 2.646lvw, 101.5px)` |
| `--lede-size` | `clamp(1.2rem, 1.693lvw, 64.96px)` |
| `--mobile-menu-text-size` | `clamp(40px, 2.307lvw, 60px)` |

Leadings: `--h1-leading: 1.15`, `--h2-leading: .9` (tight, deliberate),
`--h3-leading: 1.1`, `--h4/h5/h6-leading: 1.2`. Body copy `line-height: 1.2`.

Measured at 1440x900: h1 = 57.14px / 65.71px; h2 = 38.10px / 34.29px;
p = 15px / 18px; card title (h3) = 13px/700; card body = 13px / 15.6px;
nav link = 13px UPPERCASE with `padding-bottom: --nav-underline-offset`.

## 4. Spacing / layout tokens (verbatim)

`--spacing-2xs` `clamp(4px,.265lvw,10.2px)` - `--spacing-xs` `clamp(8px,.661lvw,25.4px)` -
`--spacing-s` `clamp(16px,1.323lvw,50.8px)` - `--spacing-m` `clamp(20px,1.984lvw,76.1px)` -
`--spacing-l` `clamp(24px,2.646lvw,101.5px)` - `--spacing-xl` `clamp(24px,3.307lvw,127px)` -
`--spacing-2xl` `clamp(40px,4.96lvw,190.4px)` - `--spacing-3xl` `clamp(50px,6.614lvw,253.8px)` -
`--spacing-4xl` `clamp(60px,8.34lvw,320px)`.
`--gutter` `clamp(24px,3.307lvw,127px)`, `--gutter-lg` `clamp(48px,6.614lvw,254px)`,
`--gutter-xl` `clamp(96px,13.228lvw,507.6px)`, `--grid-gap` `clamp(24px,3.307lvw,127px)`,
`--radius-s` `clamp(4px,.331lvw,12.7px)`, `--spacing-card` `clamp(20px,1.653lvw,63.5px)`,
`--spacing-nav` `clamp(20px,2.381lvw,91.4px)`, `--nav-underline-offset` `clamp(3px,.198lvw,7.6px)`,
`--btn-padding-block` `clamp(12px,.992lvw,38.1px)`, `--btn-padding-inline` `clamp(20px,1.653lvw,63.5px)`.

Content widths: `--content-narrow` `clamp(480px,47.02lvw,1805px)` (lede),
`--content-medium` `clamp(520px,51.59lvw,1980px)` (hero h1 max-width).

Height tokens are keyed to a **stable viewport height** of 900px
(`--stable-vh: 900px`), not to the live viewport:
`--hero-min-h-xs` `calc(900px*.3)`, `--hero-min-h` `calc(900px*.6)`,
`--hero-max-h` `calc(900px*.7)`, `--sticky-top` `calc(900px*.15)`,
`--gallery-item-h` `900px`, `--spacing-footer-top` `calc(900px*.09921)`.

Breakpoints: sm 480, nav 768/769, md 900, lg 1024, xl 1200, 2xl 1540.

**Radius: effectively 0.** Measured `border-radius: 0px` on header, nav, buttons,
footer, article cards, cookie panel. The single exception is the book/asset cover
surface, which uses `--radius-s` (4 -> 12.7px). No drop shadows anywhere.

## 5. Structural grammar (measured y/h at 1440x900, doc height 3693)

```
header.header           y=10   h=62   fixed, transparent
section.home-hero       y=0    h=900  full-bleed, contains dither canvas + h1
  section.dither-hero   y=10   h=880  black, holds canvas + vignette
  div.container > h1    y=452  h=141  vertically centred, max-width --content-medium
section.home-lede       y=900  h=375  lede paragraph, content width 891px
section.home-section-arc y=1275 h=575 section-header + book arc carousel
section.home-section    y=1851 h=960  "Latest release" - full-width featured image
section.articles-section y=2810 h=702 section-header + 4-col article grid
footer.footer           y=3512 h=181 full-width
```

### 5.1 Header (`.`header`)
- `position: fixed; top: calc(10px + env(safe-area-inset-top)); left:50%; translate:-50% 0;
  width: calc(100vw - 20px); z-index: 20; padding-block: --spacing-s; padding-inline: --spacing-s`.
- Background **transparent** at every scroll depth. No blur, no border, no shadow,
  never shrinks or hides.
- `.header__inner` flex, `justify-content: space-between`.
  - Left: brand mark (small white square with a knockout glyph) + wordmark, links home.
  - Right: `.header__nav` flex, `gap: --spacing-nav`, containing `.nav-link` items.
  - `.nav-link`: `font-family: var(--font-nav); font-size: var(--text-sm);
    text-transform: uppercase; text-decoration:none; padding-bottom: --nav-underline-offset;
    border-bottom: 1px solid transparent; margin-bottom: calc((--nav-underline-offset + 1px) * -1)`.
  - `.nav-link:hover`, `:focus-visible`: `border-bottom-color: var(--color-link-hover)`.
  - `.nav-link--active`: `border-bottom-color: currentColor` (persistent underline).
- Below 769px: nav hidden, `.header__menu` hamburger shown
  (`width/height clamp(27px,1.985lvw,33px)`, icon `clamp(19.5px,1.562lvw,23.6px)`),
  opening a fullscreen `.mobile-menu` overlay with `--mobile-menu-text-size` links.
- Skip link: `.skip-link` fixed top-left, white on black, `translateY(-120%)`,
  `:focus-visible` -> `translateY(0)`.

### 5.2 Hero
- `section.home-hero`: `100vw` wide, `min-height/height: var(--stable-vh)`,
  `border: 10px solid black` (the black frame around the canvas), flex centred.
- `.dither-hero` absolutely inset over it; `.dither-hero__canvas` is a WebGL canvas,
  `opacity:.92`, `touch-action: pan-y`, filled by a **dithered greyscale plasma**.
- `.dither-hero__vignette`: `radial-gradient(ellipse 80% 70% at 50% 40%, transparent 20%,
  rgba(0,0,0,.55) 100%)`, fades in only before the canvas is ready.
- The `<h1>` is centred, `font-size: var(--h1-size)`, `font-weight: 400`,
  `max-width: var(--content-medium)`, `line-height: 1.15`, uppercase, broken into
  short lines. Page-load sequence: overlay/border clears, then the headline is
  composited **inside the dither canvas** in white, emerging through the plasma.
  Implementation for this project: render the headline into an offscreen 2D canvas,
  upload as a texture, warp it with the plasma displacement field, and mask it into
  the dithered field (see 6.1). A plain DOM `<h1>` must remain in the markup for
  accessibility and as the no-WebGL fallback.

### 5.3 Lede
- `section.home-lede`: `padding: 95.24px 438.81px 95.24px 0`, i.e. an asymmetric
  block; the paragraph is `--text-md`-ish, `color` mid-grey -> white on reveal,
  max-width ~891px at 1440. Plain body text, no heading.

### 5.4 Section header (used by every content section)
- `.section-header`: flex, `align-items: flex-end`, `justify-content: space-between`,
  `gap: --spacing-m`, full width.
- `.section-header__title`: `margin:0; font-size: var(--h2-size); font-weight: 400`
  (measured 38.1px / 34.29px line-height, leading .9).
- `.section-header__action`: right-aligned link, e.g. `ALL BOOKS ->`, `ALL ARTICLES ->`,
  uppercase mono, underline-on-hover. Hidden until its reveal runs.
- Sections themselves: `padding-block: var(--spacing-2xl)` (or `--spacing-2xl 0`),
  `display:flex; flex-direction:column; gap: var(--spacing-l)`.

### 5.5 Card row / arc carousel (the signature content pattern)
- `.book-arc-carousel-shell`: `width:100vw; margin-left: calc(50% - 50vw)` (full bleed).
- `.book-arc-carousel`: `--arc-radius: 2800px; --arc-card-width: clamp(180px,31.5vw,270px);
  --arc-angle-step: 8deg`.
- `.book-arc-carousel__viewport`: `overflow:hidden; min-height: clamp(288px,46.8vh,504px)`.
- `.book-arc-carousel__ring`: absolutely positioned, `transform-origin: 50% var(--arc-radius)`,
  `will-change: transform`.
- Each `.book-arc-carousel__slot` is `rotate(var(--slot-angle))` about the same origin,
  producing the **arc** of cards that is tallest at centre. Cards scale down as they
  leave the centre: `--book-card-scale: .75` at the rim, 1 at centre; inner typography
  and padding are multiplied by that scale.
- Drag/scroll moves the ring; `cursor: grab` / `grabbing`; cards are real links.
- Container queries / mobile: the row degrades to a horizontally scrollable strip.

### 5.6 Card (`book-card` -> here: signal/asset card)
- `.book-card`: flex column.
- `.book-card__tags`: flex, `gap: --spacing-m`, `font-size: --text-xs`, `line-height:1.1`,
  `padding-bottom: --spacing-xs`. Small uppercase mono chips/numbers.
- `.book-card__cover-wrap`: `perspective: 500px` (3D tilt affordance).
- `.book-card__cover`: `aspect-ratio: 224 / 335; transition: transform .2s ease-out`.
- `.book-card__cover-surface`: `border: 1px solid var(--color-muted);
  border-radius: var(--radius-s); overflow:hidden`.
- `.book-card__text`: flex column, `gap: --spacing-xs`, `padding-top: --spacing-card`.
- `.book-card__title`: `font-size: --text-sm; font-weight:700` (measured 13px/700).
- `.book-card__subtitle`: `--text-sm`, `line-height:1.2`, mid-grey.
- `.book-card__link`: `--text-sm`, `text-decoration: underline`.
- Post-reveal hover: cover tilts (`transform`), as with `group-hover: scale(1.05)`
  on media in the wider grammar.

### 5.7 Featured block
- One full-content-width media surface (`16:9`-ish), sharp corners, monochrome
  treatment, optional `.dither-flow-overlay` canvas on the image surface (a second,
  subtler dither pass over the artwork).
- Caption below: title (`--text-sm`, 700), subtitle (`--text-sm`, mid-grey),
  underlined link.

### 5.8 Article / record grid
- `.articles-section`: `padding: var(--spacing-2xl) 0`, inner flex column, `gap: --spacing-l`.
- `.card-carousel__track`: 4 columns (2 at md, 1 at sm), `--grid-gap` between.
- `.article-card`: flex column, `gap: --spacing-card`.
- `.article-card__image`: `aspect-ratio: 1`, `transition: transform .2s ease-out`.
- `.article-card__image-surface`: `border: calc(var(--text-xs) * .05) solid var(--color-text)`
  (a hairline white frame), `overflow:hidden`.
- `.article-card__title`: `--text-sm`, 700. `__excerpt`: `--text-sm`, `line-height:1.2`.
  `__link`: `--text-sm`, underline.

### 5.9 Footer
- `.footer`: `padding: var(--spacing-footer-top) 0 var(--spacing-xl);
  background: var(--color-bg)`.
- `.footer__inner`: flex, space-between, `gap: --spacing-m`, wrap.
- Left `.footer__left`: brand mark (24px tall) + copyright paragraph, `--text-base`.
- Right `.footer__right`: flex, `gap: --spacing-nav`, `--text-base`; links are
  `min-height:44px; padding-block:8px`, hover -> `text-decoration: underline`;
  then `.footer__socials` (`gap: --spacing-s`) with 24px icon links, hover -> `opacity:.7`.
- Focus-visible on footer links: `outline: 2px solid var(--color-text); outline-offset:2px`.

### 5.10 Buttons / chips
- `.cp-btn`: mono, `padding: --btn-padding-block --btn-padding-inline`,
  `background: var(--color-btn) #333`, `color: #fff`, `border-radius: 0`,
  `transition: background .2s, color .2s`.
- `.cp-btn:hover / :focus-visible`: `background: #fff; color: #000`.
- Plain variant `.cp-btn--plain`: transparent, text-only, underline on hover.

### 5.11 Cookie notice
- Fixed bottom-right (bottom-centre under 900px), `background:#1a1a1a`,
  hairline `#333` border, `padding: 16px`, `--text-sm` mono copy, underlined
  `Privacy` and `Decline` links, filled white accept button that inverts on hover.
- Dismissal persists in `localStorage`.

## 6. Motion and interaction

- **Smooth scroll:** Lenis (`html.lenis`, `html.lenis body { height:auto }`,
  `scroll-behavior:auto`). Vendor a tiny RAF lerp implementation; do not add a
  build step. Respect `prefers-reduced-motion` by disabling it.
- **Reveals:** no CSS keyframes. Content enters on scroll with `opacity 0 -> 1`,
  `translateY(10%) -> 0`; the reference's own fallback rule is
  `:not(.is-card-reveal-ready) > * { opacity: 0; transform: translateY(10%) }`,
  i.e. elements start hidden and are revealed by a class flip. Use IntersectionObserver
  for this project (no GSAP dependency).
- **Custom cursor:** two `position:fixed` shapes, `mix-blend-mode: difference`,
  z-index 100/101, `pointer-events:none`: a 30x34 big shape and a 10x18 small shape,
  both following the pointer with different lag. On hoverable label targets
  (`[data-cursor]`) a 120x110 `cursor__label-wrap` fades in with 13px bold label
  text and chevrons, and the big shape switches to `mix-blend-mode: normal` with
  fill `--cursor-hover-color` `#48ff00` at `opacity:.9`.
  Must be **disabled on coarse pointers / touch** and re-enable the native cursor.
- **Nav roll:** nav/nav-roll links are duplicated vertically inside `overflow:hidden`;
  hover rolls the second copy up. Fast (100-150ms).
- **Hover deltas measured:** `main a / .cp-btn` -> bg `#333 -> #fff`, colour `#fff -> #000`
  (200ms); footer links -> `text-decoration: underline`; footer social -> `opacity .7`.
- **Scroll-driven horizontal rows:** the reference drives its card rows from scroll
  position, not a timer; there is no autoplay marquee and no edge-fade mask on the
  card rows (hard `overflow:hidden` crop). Keep that: no infinite auto-scroll tickers.
- **prefers-reduced-motion:** disable plasma animation, cursor, smooth scroll and
  reveals; render a static dithered frame.

### 6.1 Dither hero (implementation contract)
Reproduce the *effect*, with an original implementation:
1. Offscreen WebGL2 canvas sized to the hero, `image-rendering: pixelated`.
2. Field: a smooth, slow plasma computed per fragment (sum of a few warped
   sine/cos lobes, or the classic `p += sin(p.yx*k+t)` folding loop) producing
   luminance 0..1 in greyscale only. No colour.
3. Quantise: ordered 8x8 Bayer matrix added to `lum*255`, then snap to the
   reference's measured grey ladder **{0, 27, 48, 71, 94, 119, 145, 171, 198, 226, 255}**.
   Render at a low internal resolution (cell size ~3-6 device px) and scale up so the
   dither cells read as visible square pixels.
4. Vignette: `1 - clamp(len((uv - (0.5,0.4)) / (0.4,0.35)),0,1) * .55 * smoothstep(.2,1,shade)`.
5. Headline composite: draw the project headline (uppercase, `--h1-size`, JetBrains
   Mono, white) into a 2D canvas, upload as a texture; displace its UVs by a
   plasma-derived offset that is itself snapped to the dither grid; `mix(bg, white,
   textMask)` so the type appears *inside* the dither field.
6. First load: the title resolves out of noise over ~1.2-1.8s; then it stays legible
   and only the field moves. `prefers-reduced-motion` and no-WebGL: reveal the DOM `<h1>`.
7. Budget: single quad, no post-processing chain, no external WebGL library,
   pause the RAF loop when the hero is off-screen or the tab is hidden.

## 7. Page composition for this project

Content is ours; the layout skeleton, type scale, tokens and behaviours above are
the reference's.

### 7.1 Landing page (`/`, the project front door)
1. Header: brand mark + `CROSS-ASSET MONITOR` wordmark; nav `DASHBOARD`,
   `INCIDENTS`, `METHOD`, `GITHUB`.
2. Hero: dithered plasma with the headline `CROSS-ASSET / ANOMALY / MONITOR`
   resolved inside the field; eyebrow line with live status.
3. Lede: the project's one-paragraph statement.
4. Section `TRACKED MARKET` - the arc carousel, one card per instrument in the
   universe (symbol as tag, sparkline/last price as the "cover" artwork,
   title = instrument name, subtitle = last price + z-score, link = dashboard
   deep link). This is the direct translation of the reference's book arc.
5. Section `LATEST SIGNAL` - featured block: the most recent anomaly, full-width
   surface with the price chart, caption + underlined link to the alert.
6. Section `METHOD` - 4-up card grid (data -> features -> detectors -> publish),
   square hairline-framed tiles with index numbers.
7. Section `RECENT INCIDENTS` - the last four incidents as record cards.
8. Footer: brand mark + copyright, links (`Dashboard`, `Incidents`, `README`,
   `Cookies`, `Privacy`), GitHub/LinkedIn icons.
9. Cookie notice, custom cursor, skip link, mobile menu.

### 7.2 Dashboard (`/dashboard.html`)
Same design system, dense data layout. Every existing feature must survive:
- header + live `updated` timestamp chip
- **Asset status** cards (one per symbol: price, change, z-score, EWMA, state)
- **Asset detail**: symbol bar (keyboard-reachable buttons), price chart,
  correlation heatmap, correlation summary
- **Anomaly timeline & alerts**: window/severity selectors, score range slider,
  timeline chart, recent-alerts table (sortable, row -> alert detail)
- **Detector performance**: rolling-30d precision chip + per-detector table
- **Incident log** section (`#incidents`)
- **Links** section: raw JSON endpoints
- states: loading, empty, error, stale-data warning
Charts keep ECharts 5.5 (already used, free CDN) but are restyled to the
reference's palette: black plot background, `#575757` grid lines, `#8F8F8F`
axis labels, `#ffffff` marks, `#48ff00` reserved for the accent/live state.

### 7.3 Incidents (`/incidents.html`)
Keep the redirect to `dashboard.html#incidents` (update the target), styled to match.

## 8. Files

```
cross-asset-anomaly-monitor-v6.html   landing page (repo root, published as /index.html)
site/dashboard.html                   dashboard (published as /dashboard.html)
site/incidents.html                   redirect shim
site/assets/css/codapress.css         the design system (all section 2-5 tokens)
site/assets/css/dashboard.css         dashboard-only layout on top of the system
site/assets/js/lenis-lite.js          RAF smooth scroll (no dependency)
site/assets/js/reveal.js              IntersectionObserver reveals
site/assets/js/cursor.js              custom cursor
site/assets/js/nav.js                 header/mobile menu
site/assets/js/dither-hero.js         WebGL dither plasma + headline composite
site/assets/js/landing.js             landing page data binding (latest.json, etc.)
site/assets/js/dashboard.js           dashboard logic (moved out of inline <script>)
```
`scripts/publish_live_site.py` must copy `cross-asset-anomaly-monitor-v6.html`
to `build/index.html`, copy `site/**` to `build/`, and keep writing `.nojekyll`.

## 9. Constraints

- Free tiers only. No paid fonts, APIs, CDNs or build minutes. JetBrains Mono via
  Google Fonts (OFL) or a self-hosted OFL copy. No new npm build step for the site.
- No authentication, no server, no secrets client-side.
- GitHub Pages (branch `live-data`, published by `.github/workflows/live.yml`)
  remains the host: it is already live, free forever, and redeploys on every push.
- Do not copy the reference's copy, book covers, photography, logo or source code.
  Reproduce structure, tokens and behaviour only.
- Keep every existing data contract: `data/latest.json`, `data/anomalies.json`,
  `data/correlations.json`, `data/charts/<symbol>.json`, `data/feedback.json`,
  `data/precision.json`, `data/incidents.json`.
- Accessibility: skip link, focus-visible on every interactive element, WCAG AA
  contrast for body copy, no horizontal scroll at 390/768/1440, keyboard-reachable
  nav, table headers, `prefers-reduced-motion` honoured.
