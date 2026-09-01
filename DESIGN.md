---
name: Kyle's Morning Drive
colors:
  surface: '#111415'
  surface-dim: '#111415'
  surface-bright: '#373a3b'
  surface-container-lowest: '#0c0f10'
  surface-container-low: '#191c1d'
  surface-container: '#1d2021'
  surface-container-high: '#282a2b'
  surface-container-highest: '#323536'
  on-surface: '#e1e3e4'
  on-surface-variant: '#dac2ad'
  inverse-surface: '#e1e3e4'
  inverse-on-surface: '#2e3132'
  outline: '#a28d79'
  outline-variant: '#544433'
  surface-tint: '#ffb869'
  primary: '#ffc485'
  on-primary: '#482900'
  primary-container: '#ff9d00'
  on-primary-container: '#663c00'
  inverse-primary: '#885200'
  secondary: '#d0bcff'
  on-secondary: '#3b0191'
  secondary-container: '#552daa'
  on-secondary-container: '#c3abff'
  tertiary: '#cccfd8'
  on-tertiary: '#2d3137'
  tertiary-container: '#b1b4bc'
  on-tertiary-container: '#42464d'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#ffdcbb'
  primary-fixed-dim: '#ffb869'
  on-primary-fixed: '#2c1700'
  on-primary-fixed-variant: '#673d00'
  secondary-fixed: '#e9ddff'
  secondary-fixed-dim: '#d0bcff'
  on-secondary-fixed: '#23005c'
  on-secondary-fixed-variant: '#522aa7'
  tertiary-fixed: '#dfe2eb'
  tertiary-fixed-dim: '#c3c6cf'
  on-tertiary-fixed: '#181c22'
  on-tertiary-fixed-variant: '#43474e'
  background: '#111415'
  on-background: '#e1e3e4'
  surface-variant: '#323536'
typography:
  headline-xl:
    fontFamily: Space Grotesk
    fontSize: 48px
    fontWeight: '700'
    lineHeight: 56px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Space Grotesk
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 40px
  headline-lg-mobile:
    fontFamily: Space Grotesk
    fontSize: 24px
    fontWeight: '700'
    lineHeight: 32px
  body-md:
    fontFamily: JetBrains Mono
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  label-sm:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
spacing:
  base: 8px
  gutter: 16px
  margin-mobile: 20px
  margin-desktop: 40px
  stack-sm: 4px
  stack-md: 12px
  stack-lg: 24px
---

## Brand & Style

This design system is inspired by the nostalgic, lo-fi aesthetic of 16-bit pixel art, tailored for a modern podcasting experience. It balances the warmth of an early morning sunrise with the high-contrast clarity required for a technical, developer-centric audience.

The style is a fusion of **Retro-Brutalism** and **Modern Minimalism**. It utilizes sharp geometric edges, heavy high-contrast borders, and vibrant gradients to evoke the feeling of a classic adventure game interface while maintaining the usability of a premium SaaS application. The emotional response is one of energy, focus, and rhythmic consistency—mimicking the steady pace of a morning commute.

## Colors

The palette is derived directly from the "Golden Hour" of the morning sky.
- **Primary (Sunrise Orange):** Used for primary actions, progress bars, and active states.
- **Secondary (Twilight Purple):** Used for depth, backgrounds of interactive elements, and secondary buttons.
- **Tertiary (Midnight Blue):** The core background color, providing a deep, stable canvas for content.
- **Accent (Sunlight Yellow):** Reserved for high-priority alerts, play/pause toggles, and live indicators.

The design defaults to **Dark Mode** to minimize eye strain during early hours and to allow the vibrant oranges and purples to pop against the deep background.

## Typography

Typography bridges the gap between digital precision and retro gaming. 
- **Space Grotesk** is used for headlines to provide a modern, geometric, and slightly technical feel. 
- **JetBrains Mono** is used for all body text, metadata, and labels to reinforce the "pixel/developer" aesthetic, ensuring high legibility for technical podcast titles and descriptions.

All headlines should use tight letter spacing. Labels should be uppercase with slightly increased tracking to ensure they are distinct from body copy.

## Layout & Spacing

The layout follows a **Fixed Grid** approach for desktop and a **Fluid Grid** for mobile. 
- Desktop uses a 12-column grid with a max-width of 1200px.
- Mobile utilizes a single-column layout with generous side margins to prevent content from hitting the screen edge.

Spacing is strictly based on an 8px modular scale. "Stack" tokens are used for vertical spacing between elements within a card or list item to maintain a tight, organized rhythm.

## Elevation & Depth

Elevation is communicated through **Bold Borders** and **Tonal Layering** rather than traditional shadows.
- **Level 0 (Base):** Midnight Blue background.
- **Level 1 (Cards):** Deep purple surfaces with a 2px solid border in a slightly lighter purple shade.
- **Level 2 (Active/Hover):** Sunrise Orange borders.

Instead of blurs, use "dithered" patterns (stippled dots) for background transitions or 1px interior borders to simulate highlight edges, staying true to the pixel-art source material.

## Shapes

To maintain the pixel-art aesthetic, the design system uses **Sharp (0px)** corners for all primary containers, buttons, and input fields. 

Small circular elements (like status pips or avatars) are permissible, but the overall architecture should feel blocky and structural. Interactive elements should feature a 2px offset "block shadow" (a solid color offset) rather than a soft blur to give them a physical, tactile feel.

## Components

### Buttons
Primary buttons use the Sunrise Orange background with Black text, featuring a 4px bottom-right offset "shadow" in Deep Purple. The hover state moves the button 2px toward the shadow, simulating a physical press.

### Chips
Used for podcast categories (e.g., #Tech, #Morning). These are rectangular with a 1px white border and JetBrains Mono text.

### Audio Player
The core component. It should feature a large, pixel-style progress bar. The "Play" button is the largest element, rendered in Sunlight Yellow to denote its primary status.

### List Items
Podcast episodes are listed in high-contrast rows. Every row is separated by a 1px purple line. On hover, the entire row background shifts to a very dark purple tint.

### Input Fields
Used for search. Sharp corners, 2px purple border, and a flashing "block" cursor in the style of a terminal.