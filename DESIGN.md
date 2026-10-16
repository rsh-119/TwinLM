---
name: TwinLM
description: Your AI bill, reported and reduced. An annual report on AI spend, set in one ink and one orange.
colors:
  paper: "#ffffff"
  paper-2: "#f2f2f2"
  ink: "#111111"
  ink-2: "#4a4a46"
  rule: "#d9d8d2"
  signal: "#ff4f00"
  signal-ink: "#b83800"
  on-signal: "#1a0a00"
typography:
  figure:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(5rem, 13vw, 10.5rem)"
    fontWeight: 800
    lineHeight: 0.82
    letterSpacing: "-0.035em"
    fontFeature: "\"tnum\" 1, \"lnum\" 1"
    fontVariation: "\"wdth\" 62"
  display:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(2.4rem, 5vw, 4.25rem)"
    fontWeight: 750
    lineHeight: 0.98
    letterSpacing: "-0.03em"
    fontVariation: "\"wdth\" 88"
  headline:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(2rem, 4vw, 3.5rem)"
    fontWeight: 750
    lineHeight: 0.98
    letterSpacing: "-0.03em"
    fontVariation: "\"wdth\" 88"
  title:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.875rem"
    fontWeight: 750
    lineHeight: 0.98
    letterSpacing: "-0.03em"
    fontVariation: "\"wdth\" 88"
  body:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 400
    lineHeight: 1.625
    fontFeature: "\"tnum\" 1, \"lnum\" 1"
  label:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 600
    letterSpacing: "0.01em"
    fontVariation: "\"wdth\" 100"
  code:
    fontFamily: "JetBrains Mono, ui-monospace, Consolas, monospace"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.7
rounded:
  none: "0px"
spacing:
  gutter: "16px"
  gutter-wide: "32px"
  column-gap: "48px"
  row: "40px"
  block: "56px"
  section: "80px"
  section-wide: "112px"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "0 24px"
    height: "48px"
  button-primary-hover:
    backgroundColor: "{colors.signal}"
    textColor: "{colors.on-signal}"
  button-outline:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "0 24px"
    height: "48px"
  button-outline-hover:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
  button-signal:
    backgroundColor: "{colors.signal}"
    textColor: "{colors.on-signal}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "0 24px"
    height: "48px"
  button-signal-hover:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.on-signal}"
  nav-bar:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink-2}"
    typography: "{typography.label}"
    height: "64px"
  tab:
    textColor: "{colors.ink-2}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "10px 16px"
  tab-active:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
  code-block:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.code}"
    rounded: "{rounded.none}"
    padding: "16px"
---

# Design System: TwinLM

## Overview

**Creative North Star: "The Annual Report"**

The site reads as a corporate annual report on the visitor's own AI spend: white paper, near-black ink, and a single International Orange spent either at page scale (one full-bleed spread) or on money. Each spread carries one decisive figure set in heavily condensed Archivo, flanked by plain-width prose and flat data graphics. Structure comes from a strict 12-column Swiss grid and hairline rules, never from boxes, tints, or shadows.

Density is editorial rather than dashboard: generous vertical rhythm between sections (80 to 112px), tight internal rows separated by 1px rules, tabular lining numerals everywhere so figures align like a ledger. Motion is spent on the numbers only: digits roll on an odometer and chart bars ease to new heights, both with an expo-out curve and both disabled under reduced motion.

The world rejects the dark, gradient AI-SaaS hero with feature bento cards and logo strips. It also does not claim results it has not measured: illustrative figures are captioned as such in the same label voice as the chart they sit on.

**Key Characteristics:**
- One paper, one ink, one colour; orange is the only hue on the page.
- Archivo's width axis carries the hierarchy: condensed (wdth 62) for figures, semi-condensed (wdth 88) for headings, normal (wdth 100) for text.
- Square everything: zero radius on buttons, frames, slider thumbs and bars.
- Hairline rules and a 12-column grid do all the structural work; no cards, no shadows.
- Flat bar charts and ledger rows as the native data vocabulary.

## Colors

A strict monochrome ledger with a single, loud signal colour.

### Primary
- **International Orange** (signal): the only hue. Used at page scale for the one full-bleed savings spread, for the "after" bars in charts, for the headline money figure, slider thumbs, focus outlines and text selection. On white it is legible only at figure and display sizes.
- **Report Orange, Text Grade** (signal-ink): orange darkened for body-size text on white, such as string literals in code.
- **Burnt Black** (on-signal): text, rules and code frames sitting on the orange spread or on orange buttons.

### Neutral
- **Report Paper** (paper): the page ground and the text colour on ink surfaces.
- **Proof Grey** (paper-2): the focused state of inline inputs and the scrollbar track. Never a card fill.
- **Press Ink** (ink): text, primary buttons, section rules, the "before" bars, the closing full-bleed ink band.
- **Warm Graphite** (ink-2): secondary prose, asides, slider labels, inactive nav and tabs, code comments.
- **Hairline Stone** (rule): the light row dividers inside ledgers (lever rows, feature list), under a heavier ink rule that opens each list.

### Named Rules
**The One Colour Rule.** Orange is the only hue on any surface. It is spent at page scale (one spread per page) or on money and the user's own estimate; never on decoration, icons, or section tints.

**The Text-Grade Rule.** Orange text on white at body sizes uses signal-ink, never signal. Signal on white is reserved for figure and display sizes.

## Typography

**Display Font:** Archivo variable, width axis enabled (with ui-sans-serif, system-ui)
**Body Font:** Archivo at normal width
**Label/Mono Font:** JetBrains Mono, for code, config, endpoints and filenames only

**Character:** One family doing three jobs through its width axis: a narrow, heavy figure face for numbers, a semi-condensed heading face, and a plain grotesque for reading. Mono appears only where the reader would paste the text.

### Hierarchy
- **Figure** (800, wdth 62, lh 0.82, -0.035em): numbers that carry a spread. Hero estimate steps down from clamp(5rem,13vw,10.5rem) to clamp(3rem,7vw,6rem) as the value lengthens; lever figures clamp(3.5rem,7vw,6rem); report total clamp(4.5rem,11vw,9rem); ledger values 2.25rem; slider readouts 1.5rem. Also the wordmark.
- **Display** (750, wdth 88, lh 0.98, -0.03em, balanced wrap): the h1 at clamp(2.4rem,5vw,4.25rem) and the closing statement at clamp(2.5rem,6vw,5.5rem).
- **Headline** (750, wdth 88): section h2 at clamp(2rem,4vw,3.5rem), always in the left 7 columns.
- **Title** (750, wdth 88): row heads at 1.875rem (deploy, pricing) or 1.5rem (levers).
- **Body** (400, 1.125rem, lh 1.625): asides and reading text, capped at 44 to 65ch. Inline sentence text in the estimator runs at 1.25rem.
- **Label** (600, wdth 100, 0.8125rem, +0.01em, sentence case): chart captions, nav, buttons (set at 0.875 to 1rem there), tab names.
- **Code** (JetBrains Mono, 0.8125rem, lh 1.7): code blocks and config excerpts; inline code at 0.875rem.

### Named Rules
**The Width-Axis Rule.** Hierarchy is set by Archivo's wdth axis, not by adding families: 62 for figures, 88 for headings, 100 for text. Never introduce a second display face.

**The Ledger Numerals Rule.** Tabular, lining numerals are on for the whole body ("tnum", "lnum"); every figure aligns as in a printed table.

## Layout

A 12-column grid inside a 1280px container, with 16px side gutters on mobile and 32px from 640px. Columns separate by 48px. Sections stack vertically and switch to the grid at 1024px; below that, everything is a single column in source order.

Each standard section opens with a full-width ink rule, then the headline in columns 1 to 7 with an optional aside in 8 to 12, then 56px of air before the content. Section padding is 80px vertical, 112px from 1024px. Content inside sections is organised as rows separated by rules, each row a sub-grid (e.g. 3 / 5 / 4 for figure, text, config; 4 / 8 for title, code). The hero splits 7 / 5: estimate on the left, chart and controls on the right. The FAQ is indented to start at column 6.

Two full-bleed bands break the paper: the orange savings spread (5 / 7 split) and the ink closing band (8 / 4 split).

## Elevation & Depth

Flat. There are no shadows anywhere. Depth is conveyed by rule weight (2px ink rules for totals and active input underlines, 1px ink for section and row boundaries, 1px stone for minor row dividers), by full-bleed colour bands, and by inversion (ink fill for active tabs and primary buttons). The sticky header uses 95% paper with a light backdrop blur so scrolled content stays legible beneath it; this is the only translucency.

### Named Rules
**The Rule-Not-Shadow Rule.** Separation is a ruled line or a colour band, never a shadow, tint panel, or floating card.

## Shapes

Square throughout: zero radius on buttons, code frames, tabs, slider thumbs and chart bars (Firefox's default thumb radius is explicitly reset to 0). Lines are the form language: solid ink rules, a dashed rule for the chart baseline, a dotted leader between feature names and their endpoint. Chart bars are flat rectangles with a 6px gap and no outline.

## Components

### Buttons
Blunt rectangular blocks, labelled in sentence case.
- **Shape:** square corners (0px), 48px tall (40px in the header), 24px horizontal padding.
- **Primary:** ink fill, paper text, label weight. Hover swaps to orange fill with burnt-black text via a colour transition.
- **Outline:** 1px ink border on paper; hover inverts to ink fill and paper text. On the ink band the border and hover fill are paper.
- **Signal:** orange fill with burnt-black text, used only on the ink closing band; hover goes to paper.
- **Text link action:** label-weight underlined text beside a primary button for the secondary path.
- **Focus:** 2px orange outline, 3px offset, global for every interactive element.
- **Icons:** a single 18px stroke arrow (up-right) marks off-site destinations; no other button icons.

### Inputs / Fields
- **Sentence input:** the monthly spend is a borderless figure-face input sitting inside a sentence, underlined by a 2px ink rule; focus adds the proof-grey fill plus the orange outline. Width tracks the value length.
- **Scale slider:** a 2px ink track with a 14 by 22px orange rectangular thumb outlined in 2px ink. Each slider sits under a 1px ink rule with its label in warm graphite and its value as a figure readout on the right.

### Navigation
Sticky 64px bar under a 1px ink rule. Wordmark in the figure face at 1.75rem; links in label weight, warm graphite, darkening to ink on hover; hidden below 768px. GitHub as an underline-on-hover text link, then a compact primary Deploy button.

### Code Block
A 1px ink frame on paper with a header strip (mono filename at 0.75rem, label-weight Copy action with a 14px icon) ruled off from the body. Two inks only for syntax: semibold ink for keywords, signal-ink for strings, warm graphite for comments. On the orange spread the frame turns burnt black with no fill. Tabs above it are flush label buttons on an ink baseline; the active tab inverts to ink.

### Ledger Rows
Lists open with a 1px ink rule and separate items with stone hairlines (or ink for heavier lists like deploy steps). The feature list uses a dotted leader to its endpoint, like a table of contents. FAQ rows are disclosure rows on ink rules with a stroke plus that rotates 45 degrees when open.

### Estimator (Signature)
The hero's live estimate. A sentence with an inline spend input resolves to a huge orange figure whose digits roll on an odometer (0.7s, expo-out). Beside it, a flat 12-bar monthly chart: ink bars before switch-on, orange bars after, a dashed baseline rule labelled with the before figure, a solid after rule, and the monthly saving set as a figure in the gap. When the after level rises within about 12% of the baseline, the two labels merge onto the baseline so they never collide. The chart caption names its source in small warm-graphite text.

### Report Spread
The full-bleed orange section: headline and prose on the left, a figure on the right captioned in label weight with "Example output, illustrative numbers", a definition-list ledger of cost lines with figure values, a 2px total rule, a giant total figure, and a two-bar comparison (burnt-black baseline, paper actual) drawn to scale.

## Do's and Don'ts

### Do:
- **Do** keep orange to one full-bleed spread per page plus money figures, chart "after" bars, slider thumbs, focus and selection.
- **Do** set every headline money figure in the figure face (wdth 62, weight 800) with tabular numerals.
- **Do** open every section and every list with a 1px ink rule, and separate rows with rules rather than gaps or boxes.
- **Do** caption every chart and illustrative figure in the label voice, stating the source or that the numbers are illustrative.
- **Do** keep all corners square (0px) and all surfaces flat.
- **Do** honour reduced motion: odometer and bar easing drop to zero duration and smooth scrolling turns off.

### Don't:
- **Don't** use a dark gradient hero, feature bento cards, or logo strips.
- **Don't** add a second hue, a gradient, or tinted panels; proof grey is a state colour, not a fill for content blocks.
- **Don't** use shadows, rounded cards, or icons inside tiles.
- **Don't** set body-size orange text on white in signal; use signal-ink.
- **Don't** use JetBrains Mono outside code, config, endpoints and filenames.
- **Don't** introduce a second display family; widen or narrow Archivo instead.
