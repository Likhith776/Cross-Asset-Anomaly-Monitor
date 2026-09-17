# Reference motion inventory — codapress.co.uk

Studied 2026-09-16 by reading the site's own bundled modules (downloaded verbatim into
`design-extraction/codapress/bundles/`) and by live inspection with Playwright.

Every value below is quoted from source or measured. Anything inferred is marked
**inferred**. This document is the input to
[`MOTION-SPEC.md`](MOTION-SPEC.md), which specifies the motion actually shipped on the
dashboard.

Source files referenced:

| Short name | File |
|---|---|
| `index.astro` | `index.astro_astro_type_script_index_0_lang.BEfZ_zs8.js` |
| `cursor` | `CustomCursor.astro_astro_type_script_index_0_lang.8yHuNaPg.js` |
| `scramble` | `link-scramble.Ch8TScXf.js` |
| `header` | `nav-active.Bs69SIeA.js` |
| `viewport` | `viewport-stable.D1uo3ZAL.js` |
| `flow` | `DitherFlowOverlay.astro_astro_type_script_index_0_lang.CKLC60sp.js` |
| `shaders` | `dither-plasma.shaders.DsyOAy8I.js` |
| `splittext` | `SplitText.YBVdmRgZ.js` (GSAP SplitText `version = "3.15.0"`) |
| `router` | `router.DjN7QAh0.js` (Astro ClientRouter) |

---

## 1. Scroll engine

Lenis 1.3.16 (`lenis.CabgN_V8.js`), used through GSAP ScrollTrigger
(`ScrollTrigger` is registered on the GSAP instance in `index.astro`).

- Container: `document.documentElement` (the `html.lenis` class is present on the root).
- `scroll-behavior: auto` is set on the root, so Lenis owns scrolling.
- Lenis drives `ScrollTrigger.update`; the site never uses native smooth scrolling.

**Reproducing without Lenis:** a RAF lerp toward a target scroll position, then drive the
reveal checks from the same loop. That is what `site/assets/js/lenis-lite.js` already does.

## 2. Entry / load sequence

- `documentElement` carries `is-home-entrance-pending` then `is-home-entrance-active`
  (read in `viewport.js` via `C()`), and dispatches `cp:home-entrance-complete`
  (listened to in `Header`). **The whole entrance is class-gated**, and the reveal
  modules wait for the entrance to finish before measuring.
- `viewport.js` also exposes `R()`/`z()` (`is-home-entrance-pending/active`) and a
  `100lvh` probe that writes `--stable-vh` in px on the root:
  `document.documentElement.style.setProperty("--stable-vh", h + "px")`.
  Touch devices get the `100lvh` value, desktop gets `visualViewport.height`.
- Page transition (Astro ClientRouter, `router.js`): swaps `documentElement`
  attributes, head children and body children; persists elements marked
  `data-astro-transition-persist` by moving them with `moveBefore` where available.
  Scripts are de-duplicated by source so they do not re-run.

## 3. Text reveals

### 3.1 Lede char-colour scrub (the signature reveal)

From `index.astro` — the most re-usable pattern on the site:

```
SplitText.create(el, { type: "words,chars",
                       wordsClass: "home-lede-word",
                       charsClass: "home-lede-char",
                       tag: "span", aria: "none",
                       deepSlice: true, autoSplit: false });
```

| Property | Value |
|---|---|
| Animated property | `color` |
| From | `#333333` |
| To | `#ffffff` |
| Per-char duration | `0.4` s |
| Per-char stagger | `0.02` s |
| Easing | `"none"` (linear) |
| Driver | `scrollTrigger` on the nearest `[data-home-lede-reveal-trigger]` |
| `start` | `"top 70%"` at ≥ 769px, `0` below |
| `end` | a computed clamp, default attribute `data-home-lede-reveal-end="top 30%"` |
| `scrub` | **`0.6`** |
| `invalidateOnRefresh` | `true` |

So the paragraph is *painted* word-by-word as you scroll — linear per character, with a
0.6 s scrub lag, over a range that ends when the trigger's top reaches 30 % of the
viewport.

### 3.2 Section heading reveal

The pre-reveal state lives in CSS, not JS:
`body > [data-page-transition-content] .card-carousel__track:not(.is-card-reveal-ready) > * { opacity: 0; transform: translateY(10%) }`
and the equivalent rule for `.books-archive__grid:not(.is-card-reveal-ready) > [data-book-item]`
and `.gallery-scroller:not(.is-gallery-reveal-ready) .gallery-scroller__item`.
**Inferred:** the JS simply flips the `is-*-reveal-ready` class when the block enters;
GSAP then animates from the CSS state.

### 3.3 SplitText mechanics

`SplitText` wraps each word and char in `span`s. With `not lines`, the wrapper structure is
`word` (`display:inline-block`) containing `char` spans (`display:inline-block`); the
plugin sets `aria-label` on the source element when `aria: "auto"` and `aria-hidden` on
the generated spans. Masks are created only when a `mask` option is passed (`overflow: clip`
wrapper carrying `*-mask` classes).

## 4. Scroll-driven behaviour

### 4.1 Featured book: width + scale reveal

From `index.astro`, the most cinematic move on the site:

```
set(container, { width: "100%", height: "auto", overflow: "hidden" });
const h = container.offsetHeight;
set(container, { width: 0, height: h, overflow: "hidden" });
set(surface,  { scale: 3, opacity: 0, transformOrigin: "center center" });
```

On intersect (rootMargin `"0px 0px 12% 0px"`, threshold 0):

| Step | Target | Values | Delay | Ease |
|---|---|---|---|---|
| 1 | container | `width: 0 → 100%`, duration `1` | 0 | `power3.inOut` |
| 2 | surface | `scale: 3 → 1`, `opacity: 0 → 1`, duration `1.5` | `0.3` | `power3.inOut` |

The image is pre-loaded (`complete && naturalWidth > 0`) before the sequence is armed, and
`clearProps: "height"` runs on completion so the container returns to auto height.

### 4.2 Featured detail slide

`set(el, { x: "5%", opacity: 0 })` → on intersect `to(el, { x: 0, opacity: 1, duration: 0.6, ease: "power3.inOut" })`.

### 4.3 Parallax

`fromTo(parallax, { yPercent: 5 }, { yPercent: -5, ease: "none",
   scrollTrigger: { trigger, start: "top 100%", end: "bottom 0%", scrub: true } })`

A ±5 % vertical parallax across the element's full travel.

### 4.4 Header capsule

From `header.js` (the module named `nav-active`): the header collapses into a capsule as
you scroll down and re-expands when you scroll up.

| Constant | Value | Meaning |
|---|---|---|
| `ee` | `1.4` | duration scale factor |
| `vt` | `0.45` | progress multiplier |
| `wt` | `20` | minimum wheel/touch delta (px) before the capsule reacts |
| `M` | `10` | minimum scrollable distance |
| `At` | `48` | `threshold = max(48, innerHeight * 0.05)` |
| `yt` | `0.5` | collapsed nav gap = `--spacing-nav * 0.5` |
| `ht / bt` | `67 / 78.15` | logo capsule width = `offsetHeight * (67 / 78.15)` |
| `mt` | `"power3.inOut"` | the timeline's default ease |
| breakpoint | `769` | below this the capsule logic is skipped |
| scrollTrigger | `id: "header-capsule"`, `start: 10`, `end: "max"` | |

Timeline: a single `power3.inOut` timeline with total duration `1.4s` (not a
delta-scaled duration; the earlier reading of `dt = |delta| * 1.4 * progress * 0.45`
was a progress formula, not a duration). Phase 1 fades the wordmark/logo wrap
(~0.56s), phase 2 animates the header width, radius `500px`, background
`rgba(255,255,255,.4)`, `backdrop-filter: blur(12px)`, chrome `#151515`,
padding `spacing-s -> spacing-m` and halves the nav gap over the remaining
~0.84s. Scroll-up reversal plays the same timeline in reverse; the trigger is
scroll position/velocity, not any upward scroll.

## 5. Hover and pointer responses

| Target | Delta | Duration | Ease | Source |
|---|---|---|---|---|
| Cursor big ball on any hover target | `scale: 1 → 4` | `0.3` | default | `cursor` |
| Cursor small ball on hover | `scale → 1`, or `0` when `data-cursor-hide-small` | `0.2` | `power2.out` | `cursor` |
| Cursor label wrap | `autoAlpha: 0 → 1` | `0.2` | default | `cursor` |
| Cursor reset (`m()`) | big `0.3`, small `0.2`, label `0.2` | | | `cursor` |
| Cursor shape fill to `data-cursor-color` | `fill → colour` | `0.2` | `power2.out` | `cursor` |
| Text link | `color → #ffffff` (the site sets `--color-link-hover`) | `100 ms` | default | `BaseLayout.css` |
| Filled button | `background #333 → #fff`, `color #fff → #000` | `0.2 s` | | measured |
| Footer link | `text-decoration → underline` | instant | | `BaseLayout.css` |
| Footer social | `opacity → 0.7` | instant | | `BaseLayout.css` |
| Card media | tilt via `perspective: 500px` + `transform .2s ease-out` | `0.2 s` | `ease-out` | `inline-styles.css` |

### 5.1 Link scramble (the hover signature)

From `scramble.js`:

| Property | Value |
|---|---|
| Character set | `ABCDEFGHIJKLMNOPQRSTUVWXYZ` |
| Duration `b` | `0.4` s |
| Per-character resolve | `random * (0.4 * 0.7) + 0.4 * 0.1` → **0.04 s – 0.32 s** |
| Update cadence | one tween over the full duration, re-randomising every frame |
| Trigger | `mouseenter` on the link |
| Gate | `(hover: hover) and (pointer: fine)` |
| Excluded | `.book-card__cover`, `.article-card__image`, `.featured-book__media`, `.further-reading__item`, links inside `.insight-prose`, and `.nav-link` |
| Restore | original text restored on completion, or immediately on an internal-link `pointerdown` and on `astro:before-preparation` |

Non-alphanumeric characters (spaces, punctuation) are never scrambled, so the word shape
stays stable while the letters flicker.

## 6. Custom cursor

| Property | Value |
|---|---|
| Shapes | `cursor__ball--big` 30 × 34, `cursor__ball--small` 10 × 18 |
| Blend | `mix-blend-mode: difference`, shape fill `#f7f8fa` |
| z-index | big `100`, label wrap `101` |
| Lerp — big ball | `0.2` |
| Lerp — small ball | `0.45` |
| Lerp — label wrap | `0.2` |
| Offsets | big `-15,-15`, small `-5,-7`, label `-60,-60` |
| Draggable target | big ball scales to `4`, label reads `DRAG` |
| Label colour | black text on the shape, shape fill switches to `--cursor-hover-color` (`#48ff00`) at `opacity .9` |
| Gates | `(hover: hover) and (pointer: fine)` **and not** `prefers-reduced-motion` |
| Handover | on `mouseleave` the code re-samples `document.elementFromPoint` and transfers to the element under the pointer |
| Reset | on an internal link `pointerdown` and on `astro:before-preparation` |

The cursor is built from SVG shapes inside the ball divs (`.cursor__shape`, filled
`#f7f8fa`), not from CSS-drawn boxes.

## 7. Image-surface dither flow

From `flow.js`. Each image surface gets its own WebGL overlay that renders a
pointer-driven, ordered-dithered "wake": the image under the wake is displaced by the
accumulated pointer flow.

| Constant | Value |
|---|---|
| Trail points | `24` |
| Dither | 8 × 8 Bayer built by bit interleaving four XOR terms, `value / 64.0` |
| Block grid | `floor(screenPixels / 5.0) * 5.0` |
| Spot radius | `mix(0.065, 0.12, energy)` |
| Soft spot | `1 - smoothstep(radius * 0.22, radius, length(delta))` |
| Mask | `step(edgeThreshold + 0.001, softSpot) * smoothstep(0, 0.2, energy)` |
| Flow clamp | `±0.055` |
| Quantisation | `5` levels, `floor(clamp(color + (bayer - 0.5) / 5, 0, 1) * 5 + 0.5) / 5` |

The `+ 0.001` bias is deliberate: Bayer contains a zero cell, and without the bias the
comparison would paint that cell across areas with no trail.

## 8. Hero: dithered plasma

From `shaders.js`. The headline is composited **inside** the fragment shader.

| Constant | Value |
|---|---|
| Iterations | `uIterations`, loop bounded at `48` |
| Grey ladder (`findClosestGray`) | `0, 27, 48, 71, 94, 119, 145, 171, 198, 226, 255` |
| Dither matrix | a hard-coded 8 × 8 ordered matrix in the 0–63 range |
| Dither cell | `uDitherCellSize`, `uDitherScale` |
| Vignette | `1 - clamp(len((uv - (0.5, 0.4)) / (0.4, 0.35)), 0, 1) * 0.55 * smoothstep(0.2, 1, shade)` |
| Text | drawn to a texture, UVs displaced by the plasma displacement field, then `mix(bg, white, textMask)` |
| Canvas opacity | `0.92` |
| Time | `uTime * uTimeSpeed`, `uModZSpeed * time` inside the modulation |

The field is monochrome; the only colour in the whole system is the cursor accent.

## 9. Section-to-section transitions

- Astro ClientRouter handles cross-page transitions (attribute/head/body swap plus
  persist elements). There is no custom overlay wipe in the page code.
- Within a page, sections are separated by class-flip reveals (section 3.2) and the
  scroll-scrubbed rows, not by wipes.
- `astro:page-load` re-initialises every module; `cp:header-refresh` and
  `cp:home-entrance-complete` are the custom events modules listen to.

## 10. Reduced motion

| Module | Behaviour under `prefers-reduced-motion: reduce` |
|---|---|
| Lede scrub | chars are set straight to `#ffffff`; no tween is created |
| Featured reveal | the ready class is added and all tweens are skipped |
| Cursor | fully disabled (`b()` returns false; the whole cursor is torn down) |
| Header capsule | `Lt()` returns true and the capsule logic is skipped |
| SplitText | still splits (structure only), no animation |

So the reference does handle reduced motion — it is not "none", it is a clean
static fallback per module.

## 11. Still to merge

The live-measurement pass (scroll sample series, scrub ratios, hover deltas, cursor
screenshots, reduced-motion screenshots) is captured separately under
`.cluster/motion/` and is merged into [`MOTION-SPEC.md`](MOTION-SPEC.md) where it changes
a value.
