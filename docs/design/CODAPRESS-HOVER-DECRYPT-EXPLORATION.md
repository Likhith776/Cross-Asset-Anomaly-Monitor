# Exploration — the reference's hover "encrypt / decrypt" effect

Reference: **https://codapress.co.uk/** · inspected 2026-09-16 · Chromium via Playwright.

## Where the code lives

The effect is a single module the site bundles as `link-scramble.Ch8TScXf.js`
(4 KB), imported by `BaseLayout.astro_astro_type_script_index_0_lang.js`. I read the
module rather than guessing from a recording, so the numbers below are the site's own.

```
const b = .4,                       // total duration, seconds
      L = "ABCDEFGHIJKLMNOPQRSTUVWXYZ",          // the only glyph set
      k = "(hover: hover) and (pointer: fine)"   // binding gate
...
const x = Math.random() * (b * .7) + b * .1;     // per-character lock time
```

Everything else falls out of those three lines.

## How it works

1. **Capture** the element's original text once, at bind time.
2. **On `mouseenter`**, replace every *alphanumeric* character with a random `A`–`Z`
   glyph and start a single animation over `b` = **400 ms**.
3. **Every frame**, any character that has not yet locked is re-randomised. Each
   character has its own lock time drawn once from
   `Math.random() * 280 ms + 40 ms` — so locks land anywhere in **40–320 ms**.
4. **Non-alphanumeric characters are never touched.** Spaces, `-`, `=`, `^`, `&`, `.`
   and arrows such as `→` stay exactly where they are, which is what keeps the shape of
   a label readable while it decodes.
5. **On completion** the original string is written back verbatim. It is also written back
   immediately on `pointerdown` for an internal link, so a click never navigates while the
   label is mid-decrypt.
6. The effect is gated to `(hover: hover) and (pointer: fine)`, i.e. it does not run on
   touch, where there is no hover to react to.

## Observed live behaviour (35 ms sampling)

| Element | Sequence |
|---|---|
| `nav a` — "Books" | `DRQks` → `BoTks` → `Books` |
| `footer a` — "Site by DH" | `KZME QM EH` → `SZAC PL HM` → `SBZI YC ZZ` → `SYtV JP JB` → `SBtB GD LF` → `SitF Sy XU` → `Site by UH` → `Site by DH` |
| `.cp-btn` — "All Books →" | `CLQ CAWQX →` → `ZFl IVGFs →` → `JYl ILSks →` → `MAl HXTks →` → `ALl EIMks →` → `XLl OPBks →` → `All Books →` |

Three things this confirms that a screenshot could not:

- the substituted glyphs are **always uppercase** while the surviving originals keep their
  own case (`BoTks` keeps a lowercase `o`, `ks`);
- the arrow in "All Books →" and the spaces in "Site by DH" **never change**;
- resolution is **progressive but not strictly left-to-right** — each character locks on its
  own clock, so short leading words finish first. That is the "decrypt" read.

## What the reference deliberately does *not* animate

Its resolver skips media and prose: card covers, article images, featured media,
further-reading items and links inside body prose. So on the reference the effect is
reserved for chrome — navigation, buttons, footer links — not for running text.

## What I implemented, and the two places I deviated

| Reference decision | Ours | Why |
|---|---|---|
| charset `A–Z`, duration 400 ms, lock 40–320 ms, one pass per hover, restore on completion and on `pointerdown` | identical | these *are* the effect |
| gated to fine pointers | identical for hover, **plus keyboard focus** | the dashboard is a keyboard-reachable product page; a hover-only reward would be unavailable to keyboard users. Focus runs it only when `:focus-visible` matches, so a click does not trigger it. |
| nav links, buttons and footer links animate | same, and also section action links, asset cards, symbol-bar buttons, incident links and alert rows | you asked for *every* clickable link and tab |
| table rows are not links | alert rows animate their **symbol cell** only | a row is a click target but scrambling seven cells at once is unreadable; the symbol cell is the row's label |
| no reduced-motion handling in the module | the whole layer is disabled before first paint | the dashboard already had this; the effect inherits it |

The alert-row and keyboard-focus additions are the only places our behaviour is a superset
of the reference. Neither changes what a hover looks like.

## Evidence

- Module source: `design-extraction/codapress/bundles/link-scramble.Ch8TScXf.js`
- Live samples: `.verify/out-scramble/reference-hover-samples.json`
- Reference mid-decrypt screenshot: `.verify/out-scramble/01-reference-mid-decrypt.png`
- Dashboard mid-decrypt screenshots: `02-dashboard-mid-decrypt-nav.png`,
  `03-dashboard-mid-decrypt-row.png`
