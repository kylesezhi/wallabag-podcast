---
name: Nocturne Editorial
colors:
  surface: '#131313'
  surface-dim: '#131313'
  surface-bright: '#393939'
  surface-container-lowest: '#0e0e0e'
  surface-container-low: '#1c1b1b'
  surface-container: '#201f1f'
  surface-container-high: '#2a2a2a'
  surface-container-highest: '#353534'
  on-surface: '#e5e2e1'
  on-surface-variant: '#d4c4b5'
  inverse-surface: '#e5e2e1'
  inverse-on-surface: '#313030'
  outline: '#9d8e81'
  outline-variant: '#50453a'
  surface-tint: '#f4bc7d'
  primary: '#f4bc7d'
  on-primary: '#482a00'
  primary-container: '#c8955a'
  on-primary-container: '#4f2e00'
  inverse-primary: '#7f5621'
  secondary: '#e6c096'
  on-secondary: '#432c0d'
  secondary-container: '#5c4221'
  on-secondary-container: '#d4af86'
  tertiary: '#c8c6c5'
  on-tertiary: '#303030'
  tertiary-container: '#a09e9e'
  on-tertiary-container: '#363636'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#ffddba'
  primary-fixed-dim: '#f4bc7d'
  on-primary-fixed: '#2b1700'
  on-primary-fixed-variant: '#643e0a'
  secondary-fixed: '#ffddb9'
  secondary-fixed-dim: '#e6c096'
  on-secondary-fixed: '#2b1700'
  on-secondary-fixed-variant: '#5c4221'
  tertiary-fixed: '#e4e2e1'
  tertiary-fixed-dim: '#c8c6c5'
  on-tertiary-fixed: '#1b1c1c'
  on-tertiary-fixed-variant: '#474746'
  background: '#131313'
  on-background: '#e5e2e1'
  surface-variant: '#353534'
typography:
  display-lg:
    fontFamily: Newsreader
    fontSize: 44px
    fontWeight: '400'
    lineHeight: 54px
    letterSpacing: -0.02em
  display-lg-mobile:
    fontFamily: Newsreader
    fontSize: 32px
    fontWeight: '400'
    lineHeight: 40px
    letterSpacing: -0.015em
  headline-lg:
    fontFamily: Newsreader
    fontSize: 32px
    fontWeight: '400'
    lineHeight: 42px
    letterSpacing: -0.01em
  headline-lg-mobile:
    fontFamily: Newsreader
    fontSize: 26px
    fontWeight: '400'
    lineHeight: 34px
    letterSpacing: -0.01em
  headline-md:
    fontFamily: Newsreader
    fontSize: 24px
    fontWeight: '400'
    lineHeight: 32px
    letterSpacing: -0.005em
  headline-sm:
    fontFamily: Newsreader
    fontSize: 20px
    fontWeight: '500'
    lineHeight: 28px
  title-md:
    fontFamily: Newsreader
    fontSize: 18px
    fontWeight: '500'
    lineHeight: 26px
  body-reading-lg:
    fontFamily: Literata
    fontSize: 20px
    fontWeight: '400'
    lineHeight: 34px
    letterSpacing: -0.003em
  body-reading-md:
    fontFamily: Literata
    fontSize: 17px
    fontWeight: '400'
    lineHeight: 28px
    letterSpacing: -0.002em
  body-ui:
    fontFamily: Hanken Grotesk
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  label-md:
    fontFamily: Hanken Grotesk
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.04em
  label-sm:
    fontFamily: Hanken Grotesk
    fontSize: 11px
    fontWeight: '600'
    lineHeight: 14px
    letterSpacing: 0.06em
  code-meta:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 18px
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  space-2xs: 0.25rem
  space-xs: 0.5rem
  space-sm: 0.75rem
  space-md: 1rem
  space-lg: 1.5rem
  space-xl: 2rem
  space-2xl: 3rem
  space-3xl: 4.5rem
  space-4xl: 6rem
  reading-column-max: 42rem
  content-column-wide: 56rem
  gutter-mobile: 1.25rem
  gutter-desktop: 2.5rem
---

## Brand & Style

This design system is tailored for sustained contemplation, long-form reading, and scholarly archiving. Evoking the classic poise of mid-century book printing transferred to a private digital sanctuary, it discards ephemeral UI trends in favor of quiet, literary permanence. 

The emotional tone is calm, focused, and scholarly. Interfaces exist solely to frame and elevate written text without competing for attention. The aesthetic language relies on:
- **Literary Minimalism:** Extreme compositional restraint, deliberate typographical rhythm, and generous margins that simulate the physical margins of a hardbound volume.
- **Warm Chromatic Balance:** A soot-and-parchment atmospheric scheme that protects against visual fatigue during nocturnal sessions.
- **Editorial Subtlety:** Structure established through hairline ruling, strict vertical alignment, and disciplined typographic scale rather than heavy containment blocks or aggressive elevation.

## Colors

The palette employs deep warm charcoals and softened bone tones, avoiding pure optical blacks (#000000) and piercing blue-tinted whites (#ffffff) to simulate low-reflectance, warm-toned matte paper viewed under gentle lamplight.

### Palette Architecture
- **Canvas / Ground Base:** `#121212` (Deep sooty charcoal ground)
- **Layer 1 Surface:** `#181818` (Recessed lists, reading canvas backdrop)
- **Layer 2 Elevated Surface:** `#1f1f1f` (Panels, flyouts, popovers, contextual toolbars)
- **Layer 3 Elevated Surface:** `#282828` (Interactive controls, highlighted card surfaces)
- **Hairline Dividers / Rules:** `#2d2d2d` (Structural borders, separators, subtle rules)
- **Interactive Border State:** `#3d3832` (Focused input edges, active item outlines)

### Typography & Ink Levels
- **Primary Ink:** `#e8e6e3` (Unbleached warm parchment, used for reading prose, primary headlines, and titles)
- **Secondary Ink:** `#d4d0ca` (Slightly muted parchment, used for subheadings, active meta, and leading paragraphs)
- **Muted Ink:** `#99948d` (Aged paper tone, used for secondary metadata, author bylaws, and read times)
- **Faint Ink:** `#6e6a64` (Faint pencil tone, used for timestamps, archival tags, footnote markers, and inactive icons)

### Accent Tones
- **Primary Accent:** `#c8955a` (Warm bookcloth amber/sepia, used sparingly for active highlights, text selections, and bookmarks)
- **Secondary Accent:** `#8a6b47` (Deep burnished bronze, used for muted hover states and progress indicators)
- **Accent Muted Surface:** `#231c14` (Warm translucent wash for selected text backgrounds or reading focus containers)

## Typography

Typography is the core architectural medium of this design system. It establishes rhythm, scale, and clarity through classical editorial craft.

### Hierarchy & Pairing
- **Headings & Titles (`Newsreader`):** An authoritative, literary serif with optical sizing that provides graceful contrast, distinguished proportions, and subtle warmth. Display and headline scales preserve character without feeling decorative.
- **Reading Body (`Literata`):** Designed explicitly for prolonged reading across screen sizes. Features a generous x-height, sturdy serifs, and intentional vertical proportion that remains comfortable under dim light conditions.
- **Interface & Micro-Meta (`Hanken Grotesk`):** A clean, understated sans-serif that remains neutral and functional. Applied to metadata, interface controls, timestamps, and utility navigation to avoid typographic noise.
- **Source Data (`JetBrains Mono`):** Reserved for technical annotations, reading speeds, progress percentages, and technical citations.

### Reading Layout Guidelines
- **Measure:** Constrain body columns to an optimal line length between 60 and 72 characters (optimal width: `680px` to `720px`).
- **Leading:** Body text employs open leading (`1.65` to `1.75`) to allow the eye to transition effortlessly from line to line.
- **Paragraphs:** Separate consecutive paragraphs with explicit vertical spacing (`1.25rem`) rather than first-line indents in digital interfaces.

## Layout & Spacing

The layout model emphasizes centered focus and expansive vertical breathing room, rejecting high-density dashboard layouts.

### Structural Model
- **Reading Views (Fixed Measure):** Centered layout constrained strictly to `reading-column-max` (`42rem` / `672px`). Generous horizontal margins expand dynamically on desktop displays to clear visual distractions.
- **Archive & Library Views (Restrained Fluidity):** Content lists and feed views align within `content-column-wide` (`56rem` / `896px`), preserving uniform scanning lines for article titles and summaries.
- **Sidebars & Shelves:** Slide-in or anchored sidebars maintain a modest width (`18rem` / `288px`), constructed with quiet borders and flush list alignments.

### Breakpoints & Adaptive Rhythm
- **Mobile (`< 640px`):** Edge-to-edge content container with horizontal gutters of `1.25rem`. Headlines step down to their mobile counterparts. Header bars condense into minimalist single-line top controls with hairline dividers below.
- **Tablet (`640px - 1024px`):** Reading measure anchors to center with minimum side margins of `2rem`. Toolbars move to an unpinned, unobtrusive header.
- **Desktop (`> 1024px`):** Content centers in the viewport with deep breathing room (`4.5rem` to `6rem` top/bottom margins). Reading views can activate an ambient, distraction-free state where non-essential navigation fades to `0%` opacity until cursor movement.

## Elevation & Depth

This system avoids floating volumetric shadows, colored glows, and intense drop-shadows. Depth is articulated exclusively through **tonal layers**, **hairline structural borders**, and **restrained ambient transitions**.

### Depth Layers
- **Ground (`#121212`):** The foundational dark matte surface for background environments and broad margins.
- **Canvas Substratum (`#181818`):** The primary reading sheet or article card baseline.
- **Raised Surfaces (`#1f1f1f`):** Contextual popovers, reading setting flyouts, and drawer drawers.
- **Interactive Elements (`#282828`):** Active toggles, hover plates, and segmented controls.

### Hairline Rules & Dividers
Separation between elements is defined by single-pixel rules using `#2d2d2d`. Cards, list items, and header chrome rely on these boundaries rather than elevation drops:
- Internal horizontal rules: `1px solid #2d2d2d`
- Overlay modal borders: `1px solid #3d3832`

### Elevation & Overlays
When absolute elevation is mandatory (e.g., article settings menus, search palettes):
- **Ambient Shadow:** `0 12px 32px -4px rgba(0, 0, 0, 0.65)`
- **Border:** `1px solid #3d3832`
- **Backdrop:** Translucent `#181818` with subtle optical blur (`backdrop-filter: blur(8px)`) to provide soft context without garish glass effects.

## Shapes

The interface balances sharp architectural lines with soft, subtle rounding. The base roundedness is `0.25rem` (4px), creating crisp edges that evoke book jackets, library index cards, and classical typography blocks.

### Corner Radii Architecture
- **Base (0.25rem / 4px):** Applied to cards, list items, input fields, checkboxes, toolbars, and drop-down menus.
- **Slight (0.125rem / 2px):** Applied to tags, inline code marks, and text highlight underlines.
- **Rounded-Lg (0.5rem / 8px):** Reserved exclusively for modal dialog sheets and floating search containers.
- **Pills (Full Radius / 9999px):** Applied only to utility badge chips, status indicators, and reading progress track indicators. Primary action buttons retain the classic soft rectangle (`0.25rem`) to maintain an understated, bookish appearance.

## Components

### Buttons
- **Primary:** Warm parchment text (`#121212`) over warm amber background (`#c8955a`). Radius: `0.25rem`. Padding: `0.5rem 1rem`. Typography: `label-md`. Hover: `#b88648` with seamless 150ms ease. No shadows.
- **Secondary / Ghost:** Transparent background with hairline border `1px solid #2d2d2d` and text `#d4d0ca`. Hover: background `#1f1f1f`, border `#3d3832`, text `#e8e6e3`.
- **Text / Minimal:** No border, text `#99948d`. Hover: text `#e8e6e3`. Padding: `0.5rem 0.75rem`.

### Chips & Metadata Badges
- Used for article publication sources, estimated read times, and tags.
- Background `#181818`, border `1px solid #2d2d2d`, border radius `9999px`.
- Typography: `label-sm` in `#99948d`.
- Active/Filter chip state: border `#8a6b47`, text `#c8955a`, background `#231c14`.

### Lists & Article Entries
- **Layout:** Quiet vertical stack. No nested card enclosures.
- **Dividers:** Each item is separated by a bottom hairline: `1px solid #2d2d2d`.
- **Content Flow:** Publication name and time-to-read in `label-sm` (`#99948d`), followed by the article headline in `headline-sm` (`#e8e6e3`), concluded with a 2-line excerpt in `body-reading-md` (`#99948d`).
- **Interaction:** Hovering an entry gently transitions the background to `#181818` with zero shift or card lift.

### Checkboxes & Radio Buttons
- Box: `16px × 16px`, background `#181818`, border `1px solid #3d3832`, radius `0.25rem` (checkboxes) or `50%` (radio buttons).
- Checked State: Background `#c8955a`, border `#c8955a`, inner checkmark/dot rendered in `#121212`.
- Focus Ring: `2px solid #8a6b47` offset by `2px` with `#121212` buffer.

### Input Fields
- Background `#181818`, border `1px solid #2d2d2d`, corner radius `0.25rem`, height `2.5rem`, padding `0 0.75rem`.
- Text color `#e8e6e3`, placeholder color `#6e6a64`, font `body-ui`.
- Focus: Border shifts to `#8a6b47` with no heavy outline or neon glow.

### Cards
- **Quiet Archive Card:** Surface `#181818`, border `1px solid #2d2d2d`, radius `0.25rem`, padding `1.25rem`.
- Free of elevation shadows. Hierarchy is achieved strictly through typographic pacing: headline in serif (`Newsreader`), source link in sans (`Hanken Grotesk`), and reading excerpt in serif (`Literata`).

### Reading Context Drawer / Control Bar
- Minimal pinned or floating bar containing typography adjustments (serif/sans switch, type scale increment, line-height presets).
- Surface: `#1f1f1f` with `1px solid #3d3832`.
- Icon buttons: Monochrome `#99948d`, shifting on hover to `#e8e6e3`, with active toggle states highlighted via warm amber `#c8955a`.

### Reading Progress Indicator
- A single horizontal line (`2px` height) affixed to the top of the viewport.
- Track ground: `#181818`.
- Progress fill: `#c8955a` (warm amber).