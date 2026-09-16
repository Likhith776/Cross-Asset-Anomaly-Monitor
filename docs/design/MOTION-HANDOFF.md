# Motion hand-off

Everything a future maintainer needs to keep, tune or delete the motion layer.

## 1. What the two files are

```
site/assets/css/motion.css   ~7.7 KB   tokens, entrance keyframes, panel wipe, hover depth
site/assets/js/motion.js     ~14.9 KB  stagger indices, decode, paragraph paint, panel reveal,
                                       count-up, live pulse, progress rail
```

They are the **only** files that carry motion. `site/dashboard.html` references them in
three places (a stylesheet link, a small inline loader, a deferred script tag) and
`site/assets/js/dashboard.js` carries five ECharts animation properties. Nothing else in
the dashboard changed.

## 2. Turning it off

| Scope | How |
|---|---|
| Whole layer, permanently | Delete the `<link href="assets/css/motion.css">`, the inline loader `<script>`, and `<script src="assets/js/motion.js">` from `site/dashboard.html`. Nothing else references them. |
| Whole layer, at runtime | Add `?motion=off` to the URL |
| Whole layer, in a build | Set `data-motion="off"` on `<html>` |
| One element | Add `data-motion-skip="M4"` (or `all`, or a space-separated list) to that element |
| Chart draw-on only | Remove the `animation*` properties from `baseOption()` in `site/assets/js/dashboard.js` |
| Users who ask for it | Automatic — `prefers-reduced-motion: reduce` disables the layer before first paint |

Effect ids: `M1` section entrance · `M2` heading decode · `M3` paragraph paint ·
`M4` panel development · `M5` card stagger + count-up · `M6` chart draw-on ·
`M7` table row stagger · `M8` live pulse · `M9` progress rail · `M11` hover depth.
`M10` (artwork parallax) is deliberately not implemented — see the spec.

## 2a. M12 — hover / focus decrypt

The effect you asked for: hovering (or keyboard-focusing) any link, button or tab
scrambles its label through random uppercase glyphs and resolves it back in ~400ms.

**Where it lives.** `site/assets/js/motion.js`, section "M12 — hover / focus decrypt"
(search for `M12`). There is no per-element code anywhere: every control is bound by
`scBind()`, and nothing was added to `site/dashboard.html` or `site/assets/js/dashboard.js`
for it.

**Tuning.** The four constants at the top of that section:

```js
var SC_CHARS   = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";  // glyph set
var SC_MS      = 400;                           // total duration (reference: 0.4s)
var SC_LOCK_MIN  = 40;                          // earliest a character locks
var SC_LOCK_SPAN = 280;                         // lock window, so 40-320ms
```

`SC_BIND` is the selector list of controls that opt in, `SC_SKIP` the exclusions, and
`SC_LABEL` the two controls whose label is a child rather than the whole element
(`.asset-card`, `tr[data-selectable]`). All four are in the same block.

**Applying it to a future element.** Either add its selector to `SC_BIND`, or mark the
element up with the attribute form the utility already supports:

```html
<a href="/x" data-scramble=".my-label"><span class="my-label">Signals</span></a>
```

`data-scramble` takes an inner selector; if the whole element is the label you can also
just call it directly:

```js
window.CPScramble.run(document.querySelector(".my-thing"));
```

**Turning it off for one element:** `data-scramble-skip` on the control, or
`data-motion-skip="M12"`. Turning off the whole layer disables it too.

**What it will not touch.** Controls with no text of their own are skipped by design — on
the dashboard that is the hamburger button and the icon-only GitHub link (2 of 213
matched controls). Everything else with a text label is bound: 211 of 213.

Each control picks exactly one text target, so a card with several text children
scrambles its label and leaves its price and metadata readable.

## 3. Tuning

Every timing lives in one place, the `:root` block at the top of `motion.css`:

```css
--motion-fast:    200ms   /* hover deltas                          */
--motion-base:    400ms   /* decode duration (mirrored in motion.js) */
--motion-slow:    600ms   /* section entrance                      */
--motion-slower:  900ms   /* panel wipe, live pulse                 */
--motion-hero:   1500ms   /* panel surface scale                    */
--motion-stagger:  60ms   /* section stagger                        */
--ease-out:   cubic-bezier(.16,.84,.44,1)
--ease-inout: cubic-bezier(.65,0,.35,1)
```

Three values are duplicated as constants in `motion.js` because they drive a
requestAnimationFrame loop rather than CSS: `DECODE_MS` (400), `PAINT_PER_CHAR` (400) and
`PAINT_STAGGER` (20), plus `COUNT_MS` (700) for the price count-up. Change them together
with the CSS to keep the two halves consistent.

Stagger counts are capped in `motion.js`: sections are indexed as-is, but cards are capped
at index 12 and alert rows at index 30, so a 139-row table does not animate for four
seconds.

## 4. How each effect is wired

| Effect | Mechanism | Why |
|---|---|---|
| M1, M5 entry, M7, incidents | Pure CSS `@keyframes` with `animation-delay: calc(var(--i,0) * …)`. `motion.js` only sets `--i`. | If the script fails, the animation still completes — content can never be left hidden. |
| M4 panel reveal | Class flip `.is-inview` on CSS **transitions of `transform` and `opacity`**, plus `.is-settled` releasing a temporary `overflow: hidden`. | A transition always lands exactly on the class's value, so a panel can never stall mid-animation. The temporary clip keeps the scaled canvas out of page overflow. `clip-path` is deliberately **not** animated — measured at 9-10 % dropped frames. |
| M2 decode, M3 paint, M5 count-up | `requestAnimationFrame` loops in `motion.js`. | They animate text/number content, which CSS cannot interpolate per character. |
| M6 chart draw-on | ECharts options in `dashboard.js`. | It is the chart library's own animation; nothing to hand-roll. |
| M9 rail | One `position: fixed` element with `transform: scaleY(var(--p))`. | `transform` only, so it never triggers layout. |
| M12 hover decrypt | One `requestAnimationFrame` loop per hovered control, cancelled on completion, `blur` or `pointerdown`. | The loop only exists while a label is resolving; a settled page reports `CPScramble.stats().active === 0`. |
| M11 hover depth | CSS `transform: perspective(500px) …` inside `@media (hover:hover) and (pointer:fine)`. | Touch devices never get a stuck hover state. |

## 5. Content safety rules the code follows

These are the invariants that make the layer safe on a data page. Keep them if you edit it.

1. **`M5` count-up writes the original string back.** The rendered value is captured first,
   then animated, then the captured string is written verbatim on the final frame. A
   non-numeric value (`—`, `n/a`) is skipped entirely.
2. **`M2` and `M8` restore the original text.** The source text is stored before scrambling
   and written back when the effect ends, so the DOM never keeps a scrambled heading.
3. **`M3` is colour-only.** The paragraph is split into spans but the full text stays in the
   DOM and in the accessibility tree; only `color` changes.
4. **`M4` never animates `width` or `height`.** Layout is untouched, so ECharts never has to
   resize mid-animation.
5. **`M9` uses `transform` only**, so the rail costs no layout.
6. **Every scroll listener is `{ passive: true }`** and coalesced through a single
   `requestAnimationFrame` guard.
7. **`M12` preserves the character count and writes the original string back.** The
   scrambled glyphs come from a monospace-safe set, so a scrambled label occupies the same
   box as the real one — measured across all 204 measurable controls at **0px width and
   height delta**. The label's accessible name is pinned to the real text for the duration
   of the pass, and removed afterwards, so assistive technology never reads scrambled
   characters.
8. **`motion.js` never writes application state.** It only reads the DOM after
   `dashboard.js` has rendered, re-armed by a `MutationObserver` on `#cards`,
   `#alerts tbody` and `#incidents-host`.

## 6. Failsafes

| Risk | Guard |
|---|---|
| `motion.js` never loads | The inline loader removes `motion-armed` after 3s, releasing every pre-state |
| The panel observer never fires | A viewport-scoped sweep on scroll and resize, plus one at 6s, reveals only panels actually in view |
| An element is left mid-animation | Transitions (M4, M11) and `both`-filled keyframes (M1, M5, M7) always end on the class/final value |
| A user has reduced motion enabled | The layer is disabled before first paint; verified that nothing stays hidden |
| Repeated visits | No state is persisted, and `data-motion-*` markers are set once per element per load |
| A decrypt loop never ends | The loop is bounded by `SC_MS`; `blur` and `pointerdown` cancel it early. `CPScramble.stats()` reports `active` / `maxActive` so a runaway is visible |
| A detached element mid-pass | Rows re-rendered by `dashboard.js` are re-bound by the existing `MutationObserver`; the old element is discarded with its loop |

## 7. Verification harness

The checks live in `.verify/` (git-ignored) and need Playwright:

```
node .verify/verify-motion.js    <baseUrl> <buildDir> <outDir>   # 26 checks
node .verify/verify-unchanged.js                                  # 12 content comparisons
node .verify/verify-site.js      <baseUrl> <outDir>               # responsive/a11y/link/states
```

`verify-unchanged.js` renders the previous commit's dashboard beside the current one and
compares element counts, headings, card prices, table headers, the first alert row, the
status line, the publish timestamp, the precision chip and table, the correlation summary,
the link count and the full normalised visible text. All twelve matched after the motion
work.

## 8. Where this came from

`docs/design/CODAPRESS-MOTION-INVENTORY.md` records the reference site's motion with the
values quoted from its own bundled modules. `docs/design/MOTION-SPEC.md` is the
specification for what was actually built here. Where this implementation deviates from the
reference, the deviation is stated in the spec with its reason rather than left implicit.
